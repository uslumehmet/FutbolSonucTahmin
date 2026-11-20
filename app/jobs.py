"""Zamanlanmış işler ve bilgisayar kapalıyken kaçırılanların telafisi."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import CLUB_LEAGUES, TZ, api_key, current_season_start, get_settings, set_value
from app.models import JobRun

log = logging.getLogger(__name__)
_LOCK = threading.Lock()
BACKFILL_PER_DAY = 30
PREDICT_DAYS = 7  # maçlar sayfasındaki '7 gün' görünümü
ESPN_RECENT_DAYS = 10  # günlük güncellemede ESPN'den son kaç günün sonuçları tamamlanır


def make_api(session):
    from app.data.api_football import ApiFootball

    key = api_key(session)
    return ApiFootball(session, key, daily_limit=get_settings(session).api_daily_limit) if key else None


def mark_job(sf, job: str, now: datetime) -> None:
    key = now.astimezone(TZ).date().isoformat()
    with sf() as s:
        row = s.query(JobRun).filter_by(job=job, run_key=key).first()
        if row is None:
            s.add(JobRun(job=job, run_key=key, finished_at=now))
        else:
            row.finished_at = now
        s.commit()


def _done_today(s, job: str, now: datetime) -> bool:
    key = now.astimezone(TZ).date().isoformat()
    return s.query(JobRun).filter_by(job=job, run_key=key).first() is not None


def _done_within(s, job: str, now: datetime, days: int) -> bool:
    return s.query(JobRun).filter(JobRun.job == job, JobRun.finished_at >= now - timedelta(days=days)).first() is not None


UPDATE_FROM_HOUR = 6  # oranlar 20 saatte bir yenilenir; gece çekilen oran öğlen oyununda bayat kalır


def _report(s, key: str, rep, now: datetime) -> None:
    data = asdict(rep) if hasattr(rep, "__dataclass_fields__") else dict(rep)
    data["at"] = now.isoformat()
    set_value(s, key, data)


def daily_update(sf, now: datetime) -> None:
    from app.data.ingest_api import backfill_dates, gap_start, ingest_results, ingest_upcoming
    from app.model.predict import predict_upcoming
    from app.model.store import ModelStore

    with _LOCK, sf() as s:
        api = make_api(s)
        rep = ingest_upcoming(s, api, now)
        res = ingest_results(s, api, now)
        rep.results, rep.errors, rep.fallbacks = res.results, rep.errors + res.errors, rep.fallbacks + res.fallbacks
        if api is not None:
            today = now.astimezone(TZ).date()
            bf = backfill_dates(s, api, gap_start(s, today), today - timedelta(days=1), max_requests=BACKFILL_PER_DAY)
            rep.errors += bf.errors
            rep.results += bf.results
        try:  # ikinci kaynak: API'nin göremediği günlerin sonuçları (ESPN); hata verirse güncelleme sürer
            from app.data.espn import ingest_espn
            from app.data.espn import ingest_summaries
            today = now.astimezone(TZ).date()
            es = ingest_espn(s, today, recent_days=ESPN_RECENT_DAYS, max_requests=40)
            rep.results += es.results
            rep.errors += [f"ESPN: {e}" for e in es.errors] + [f"ESPN eşleşmeyen: {u}" for u in es.unresolved]
            # biten maçların şut, topla oynama ve ilk 11 bilgisi (maç sayfası, Kuponlarım ve model için)
            sm = ingest_summaries(s, seasons=[current_season_start(today)], max_requests=120)
            rep.errors += [f"ESPN ayrıntı: {e}" for e in sm.errors[:5]]
        except Exception as exc:  # noqa: BLE001 - ikinci kaynak asıl işi durdurmamalı
            log.exception("ESPN hatası")
            rep.errors.append(f"ESPN: {exc}")
        preds = predict_upcoming(s, ModelStore(s), now, days=PREDICT_DAYS)
        if s.get_bind().dialect.name != "sqlite":  # bilgisayardaki önbellek olduğu gibi kalır
            from app.data.api_football import prune_old_bodies
            prune_old_bodies(s, now)
        _report(s, "last_update", rep, now)
        log.info("güncelleme: %s maç, %s tahmin", rep.fixtures, len(preds))
    mark_job(sf, "update", now)


def daily_autoplay(sf, now: datetime) -> None:
    from app.betting.autoplay import run_autoplay

    with _LOCK, sf() as s:
        if _done_today(s, "autoplay", now):  # ikinci çağrı, ilk raporun üzerine boş rapor yazmasın
            return
        _report(s, "last_autoplay", run_autoplay(s, now), now)


def settle_job(sf, now: datetime) -> None:
    from app.betting.settlement import settle_all
    from app.data.ingest_api import ingest_results

    with _LOCK, sf() as s:
        api = make_api(s)
        ingest_results(s, api, now)
        _report(s, "last_settle", settle_all(s, now), now)
        try:  # artan kotayla maça yakın oranlar; hata verirse sonuçlandırma etkilenmez
            late_odds_update(s, api, now)
        except Exception:  # noqa: BLE001
            log.exception("maça yakın oran yenileme hatası")


def late_odds_update(s, api, now: datetime) -> int:
    """Maçına birkaç saat kalan maçların oranını yeniler ve tahminlerini oranla yeniden birleştirir."""
    from app.data.ingest_api import refresh_late_odds
    from app.model.predict import _store, predict_match
    from app.model.store import ModelStore
    from app.models import Match

    ids = refresh_late_odds(s, api, now)
    if ids:
        store = ModelStore(s)
        for mid in ids:
            _store(s, predict_match(s, store, s.get(Match, mid), now), now)
        s.commit()
        set_value(s, "last_late_odds", {"at": now.isoformat(), "matches": len(ids)})
    return len(ids)


def refresh_sources(sf, now: datetime) -> None:
    from app.data.ingest import ingest_international, ingest_openfootball
    from app.model.predict import predict_upcoming
    from app.model.store import ModelStore

    with _LOCK, sf() as s:
        today = now.astimezone(TZ).date()
        ingest_openfootball(s, [current_season_start(today)], today=today, now=now)
        ingest_international(s)
        predict_upcoming(s, ModelStore(s), now, days=PREDICT_DAYS)  # yeni gelen fikstürlerin tahmini hemen çıksın
    mark_job(sf, "refresh", now)


def weekly_retrain(sf, now: datetime) -> None:
    from app.backtest.walkforward import train_layer2_all, walkforward_club

    with _LOCK, sf() as s:
        season = current_season_start(now.astimezone(TZ).date())
        for lg in CLUB_LEAGUES:
            walkforward_club(s, lg.code, season - 1, season)
        train_layer2_all(s)
        from app.betting.stats import refresh_league_accuracy
        refresh_league_accuracy(s, now)  # Performans'taki lig lig doğruluk tablosu
    mark_job(sf, "retrain", now)


def _now() -> datetime:
    return datetime.now(UTC)


def _safe(fn, sf):
    def run():
        try:
            fn(sf, _now())
        except Exception:  # zamanlayıcı iş parçacığı çökmemeli
            log.exception("zamanlanmış iş hatası: %s", fn.__name__)
    return run


def create_scheduler(sf) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=TZ, job_defaults={"coalesce": True, "misfire_grace_time": 3600})
    sched.add_job(_safe(daily_update, sf), CronTrigger(hour=10, minute=30, timezone=TZ), id="update")
    sched.add_job(_safe(daily_autoplay, sf), CronTrigger(hour=11, minute=0, timezone=TZ), id="autoplay")
    sched.add_job(_safe(settle_job, sf), CronTrigger(hour="9,15,20", minute=0, timezone=TZ), id="settle")
    sched.add_job(_safe(settle_job, sf), CronTrigger(hour=0, minute=30, timezone=TZ), id="settle_night")
    sched.add_job(_safe(weekly_retrain, sf), CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=TZ),
                  id="retrain")
    sched.add_job(_safe(refresh_sources, sf), CronTrigger(day_of_week="tue,fri", hour=6, minute=0, timezone=TZ),
                  id="refresh")
    sched.add_job(_safe(_periodic_catch_up, sf), IntervalTrigger(minutes=30, timezone=TZ), id="catchup")
    return sched


def _periodic_catch_up(sf, now: datetime) -> None:
    """Bilgisayar uykudan uyandığında kaçırılan günlük işleri yakalar."""
    catch_up(sf, now, settle=False)


def catch_up(sf, now: datetime, settle: bool = True) -> list[str]:
    """Kaçırılan işleri yapar. Açılışta settle=True; 30 dakikalık kontrolde settle=False (kota tasarrufu)."""
    ran = []

    def run(name, fn):
        try:
            fn(sf, now)
        except Exception:
            log.exception("telafi işi hatası: %s", name)
        ran.append(name)

    if settle:
        run("settle_job", settle_job)
    with sf() as s:
        # Bugünün güncellemesi sabah UPDATE_FROM_HOUR'dan sonra yapılmış olmalı; gece yarısından sonra
        # (ör. 00:16'da) yapılan güncelleme o günün oranları için sayılmaz.
        morning = now.astimezone(TZ).replace(hour=UPDATE_FROM_HOUR, minute=0, second=0, microsecond=0)
        need_update = now.astimezone(TZ).hour >= UPDATE_FROM_HOUR and not s.query(JobRun).filter(
            JobRun.job == "update", JobRun.finished_at >= morning).first()
    if need_update:
        run("daily_update", daily_update)
    with sf() as s:
        need_autoplay = now.astimezone(TZ).hour >= 11 and not _done_today(s, "autoplay", now)
        need_refresh = not _done_within(s, "refresh", now, 4)
        need_retrain = not _done_within(s, "retrain", now, 8)
    if need_autoplay:
        run("daily_autoplay", daily_autoplay)
    if need_refresh:
        run("refresh_sources", refresh_sources)
    if need_retrain:
        run("weekly_retrain", weekly_retrain)
    return ran
