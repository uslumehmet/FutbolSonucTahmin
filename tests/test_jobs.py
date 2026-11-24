from datetime import datetime

from app import jobs
from app.config import TZ
from app.models import JobRun


def _stub(monkeypatch):
    ran = []
    for name in ("daily_update", "daily_autoplay", "settle_job", "refresh_sources", "weekly_retrain"):
        monkeypatch.setattr(jobs, name, lambda sf, now, _n=name: ran.append(_n))
    return ran


def test_catch_up_runs_missing_jobs(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    names = jobs.catch_up(session_factory, datetime(2026, 11, 16, 14, 0, tzinfo=TZ))
    assert ran[:3] == ["settle_job", "daily_update", "daily_autoplay"]
    assert set(names) == set(ran)
    assert {"refresh_sources", "weekly_retrain"} <= set(ran)


def test_catch_up_in_the_morning_plays_right_after_update(session_factory, monkeypatch):
    """Bilgisayar sabah açılınca kupon beklemeden kurulur (eskiden 11:00'i bekliyordu)."""
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 16, 9, 0, tzinfo=TZ))
    assert ran.index("daily_update") < ran.index("daily_autoplay")


def test_catch_up_at_night_updates_and_plays(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 16, 2, 30, tzinfo=TZ))
    assert ran.index("daily_update") < ran.index("daily_autoplay")


def test_catch_up_at_night_does_not_repeat_a_fresh_update(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    with session_factory() as s:
        s.add(JobRun(job="update", run_key="2026-11-16", finished_at=datetime(2026, 11, 16, 0, 40, tzinfo=TZ)))
        s.commit()
    jobs.catch_up(session_factory, datetime(2026, 11, 16, 2, 30, tzinfo=TZ))
    assert "daily_update" not in ran


def test_catch_up_does_not_repeat_done_jobs(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    now = datetime(2026, 11, 16, 14, 0, tzinfo=TZ)
    with session_factory() as s:
        for job in ("autoplay", "update", "refresh", "retrain"):
            s.add(JobRun(job=job, run_key="2026-11-16", finished_at=now))
        s.commit()
    jobs.catch_up(session_factory, now)
    assert ran == ["settle_job"]


def test_catch_up_redoes_update_made_after_midnight(session_factory, monkeypatch):
    """Gece 00:16'daki güncelleme sabah oranlarının yerini tutmaz; 09:07'de yeniden yapılır."""
    ran = _stub(monkeypatch)
    with session_factory() as s:
        s.add(JobRun(job="update", run_key="2026-11-18", finished_at=datetime(2026, 11, 18, 0, 16, tzinfo=TZ)))
        s.commit()
    jobs.catch_up(session_factory, datetime(2026, 11, 18, 9, 7, tzinfo=TZ))
    assert "daily_update" in ran


def test_scheduler_jobs_registered(session_factory):
    sched = jobs.create_scheduler(session_factory)
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"update", "autoplay", "settle", "settle_night", "retrain", "refresh", "catchup"}
    autoplay = sched.get_job("autoplay")
    fields = {f.name: str(f) for f in autoplay.trigger.fields}
    assert fields["hour"] == "11" and fields["minute"] == "0"
    assert str(sched.timezone) == "Europe/Istanbul"


def test_mark_job_records_run(session_factory):
    now = datetime(2026, 11, 16, 14, 0, tzinfo=TZ)
    jobs.mark_job(session_factory, "update", now)
    jobs.mark_job(session_factory, "update", now)
    with session_factory() as s:
        assert s.query(JobRun).filter_by(job="update").count() == 1


def test_periodic_catch_up_skips_settle(session_factory, monkeypatch):
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 16, 14, 0, tzinfo=TZ), settle=False)
    assert "settle_job" not in ran and "daily_autoplay" in ran


def test_scheduler_has_periodic_catch_up(session_factory):
    sched = jobs.create_scheduler(session_factory)
    job = sched.get_job("catchup")
    assert job is not None and job.trigger.interval.total_seconds() == 1800


def test_late_odds_update_repredicts_with_fresh_odds(session, monkeypatch):
    from datetime import timedelta

    from app.model.predict import latest_prediction
    from app.models import Match, Prediction
    from tests.helpers import NOW, seed_synthetic_league

    ids = seed_synthetic_league(session, upcoming=1)
    m = session.get(Match, ids[0])
    m.api_fixture_id = 777
    session.commit()

    class Api:
        daily_limit = 100

        def used_today(self):
            return 20

        def odds_for_fixture(self, fid, max_age=None):
            vals = [{"value": "Home", "odd": "1.50"}, {"value": "Draw", "odd": "4.20"}, {"value": "Away", "odd": "6.50"}]
            return [{"fixture": {"id": fid}, "bookmakers": [{"id": 8, "bets": [{"name": "Match Winner", "values": vals}]}]}]

    now = m.kickoff_utc - timedelta(hours=3)
    assert jobs.late_odds_update(session, Api(), now) == 1
    p = latest_prediction(session, m.id)
    assert p.created_at == now and p.layers.get("piyasa") == 1
    assert p.probs["MS1"] > 0.55  # yeni oran ev sahibini açık favori gösteriyor (1.50)
    assert session.query(Prediction).filter_by(match_id=m.id).count() == 1
    assert NOW < now


def _settle_setup(session_factory, monkeypatch, api_errors):
    """Kullanıcı kuponu: bugünkü maçta MS 1. API sonuç veremezse ESPN'den gelen sonuçla tik/çarpı oluşmalı."""
    from datetime import UTC, date

    from app.data import espn as espn_mod
    from app.data import ingest_api
    from app.data.ingest_api import IngestReport
    from app.models import Bet, Competition, Coupon, CouponPick, Match, Team, Wallet

    with session_factory() as s:
        comp = s.query(Competition).filter_by(code="T1").one()
        a, b = Team(name="Ev", kind="club"), Team(name="Dep", kind="club")
        s.add_all([a, b])
        s.flush()
        m = Match(competition_id=comp.id, season=2026, kickoff_utc=datetime(2026, 11, 21, 13, 0, tzinfo=UTC),
                  home_team_id=a.id, away_team_id=b.id, status="scheduled", stats={})
        w = Wallet(name="Benim Kasam", owner="user", start_balance=100000, balance=99000, stake_rule="fixed",
                   stake_value=1000)
        s.add_all([m, w])
        s.flush()
        c = Coupon(mode="safe", source="user", target_odds=2, total_odds=1.5, prob=0.6, status="open",
                   play_date=date(2026, 11, 21))
        c.picks = [CouponPick(match_id=m.id, market="MS1", odds=1.5, prob=0.6)]
        s.add(c)
        s.flush()
        s.add(Bet(wallet_id=w.id, coupon_id=c.id, stake=1000))
        s.commit()
        match_id, pick_id = m.id, c.picks[0].id

    monkeypatch.setattr(jobs, "make_api", lambda s: object())
    monkeypatch.setattr(ingest_api, "ingest_results",
                        lambda s, api, now: IngestReport(errors=list(api_errors)))
    espn_calls = []

    def fake_espn(s, today, **kw):
        espn_calls.append((today, kw))
        m = s.get(Match, match_id)
        m.status, m.home_goals, m.away_goals = "finished", 2, 0
        s.commit()
        return espn_mod.EspnReport(results=1)

    monkeypatch.setattr(espn_mod, "ingest_espn", fake_espn)
    monkeypatch.setattr(jobs, "late_odds_update", lambda s, api, now: 0)
    return pick_id, espn_calls


def test_settle_uses_espn_when_api_quota_is_out(session_factory, monkeypatch):
    from app.config import get_value
    from app.models import CouponPick

    pick_id, espn_calls = _settle_setup(session_factory, monkeypatch, ["sonuçlar: kota doldu"])
    now = datetime(2026, 11, 21, 20, 10, tzinfo=TZ)
    jobs.settle_job(session_factory, now)
    assert len(espn_calls) == 1 and espn_calls[0][1].get("live") is True
    with session_factory() as s:
        assert s.get(CouponPick, pick_id).result == "won"
        rep = get_value(s, "last_settle")
    assert rep["picks"] == 1 and "sonuçlar: kota doldu" in rep["errors"]


def test_settle_skips_espn_when_api_works(session_factory, monkeypatch):
    _, espn_calls = _settle_setup(session_factory, monkeypatch, [])
    jobs.settle_job(session_factory, datetime(2026, 11, 21, 20, 10, tzinfo=TZ))
    assert espn_calls == []


def test_daily_update_takes_upcoming_matches_and_odds_from_espn(session_factory, monkeypatch):
    from app.config import get_value
    from app.data import espn as espn_mod
    from app.data import espn_upcoming, ingest_api
    from app.model import predict

    calls = []
    monkeypatch.setattr(ingest_api, "ingest_results", lambda s, api, now: ingest_api.IngestReport())
    monkeypatch.setattr(espn_mod, "ingest_espn", lambda *a, **k: espn_mod.EspnReport())
    monkeypatch.setattr(espn_mod, "ingest_summaries", lambda *a, **k: espn_mod.SummaryReport())
    monkeypatch.setattr(predict, "predict_upcoming", lambda *a, **k: [])

    def fake_upcoming(s, now, **kw):
        calls.append(kw)
        return espn_upcoming.UpcomingReport(matches=7, odds=5, created=1, unresolved=["Süper Lig 2026: X"])

    monkeypatch.setattr(espn_upcoming, "ingest_espn_upcoming", fake_upcoming)
    jobs.daily_update(session_factory, datetime(2026, 11, 24, 9, 7, tzinfo=TZ))
    assert calls and calls[0]["api_is_live"] is False
    with session_factory() as s:
        rep = get_value(s, "last_update")
    assert any("ESPN" in f and "7 yaklaşan maç" in f and "5 maçta oran" in f for f in rep["fallbacks"])
    assert "ESPN eşleşmeyen: Süper Lig 2026: X" in rep["errors"]
