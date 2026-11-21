"""API-Football'dan fikstür, oran, eksik oyuncu ve sonuç yükleme."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.config import CLUB_LEAGUES, COMPETITION_BY_API_ID, TZ, current_season_start
from app.data.api_football import ApiError, QuotaExceeded
from app.data.api_parse import parse_api_fixture, parse_api_injury, parse_api_odds, parse_api_player
from app.data.http import fetch_text
from app.data.ingest import competition_teams, find_match, ingest_openfootball, matches_of_team
from app.data.teams import add_alias, resolve_team
from app.models import Competition, Match, MissingPlayer, OddsQuote, PlayerSeasonStat, Team

MAX_PLAYER_LOOKUPS = 60  # eksik oyuncu istatistiği: bir güncellemede en fazla bu kadar istek (sakin günlerde)
API_RESERVE = 15  # günün geri kalanındaki sonuçlandırma çalıştırmaları için bırakılan istek
ODDS_REFRESH_HOURS = 20
# Ücretsiz plan bu sezonun oyuncu istatistiğini vermiyorsa izin verilen son sezon kullanılır;
# takım toplamları o zaman genel bir lig ortalamasıyla yaklaşık alınır.
APPROX_TEAM_GAMES = 38
APPROX_TEAM_GOALS = 53
NEUTRAL_COMPETITIONS = {"WC", "EURO"}


@dataclass
class IngestReport:
    fixtures: int = 0
    odds: int = 0
    missing: int = 0
    results: int = 0
    fallbacks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _api_team(session, comp: Competition, name: str, api_id: int, season: int) -> Team:
    kind = "national" if comp.kind == "national" else "club"
    team = session.query(Team).filter_by(api_id=api_id, kind=kind).first()
    if team is not None:
        return team
    if kind == "national":
        cands = session.query(Team).filter_by(kind="national", is_temp=False).all()
    else:
        recent = {r[0] for r in session.query(Match.home_team_id).filter(
            Match.competition_id == comp.id, Match.season >= season - 1)}
        recent |= {r[0] for r in session.query(Match.away_team_id).filter(
            Match.competition_id == comp.id, Match.season >= season - 1)}
        cands = session.query(Team).filter(Team.id.in_(recent), Team.is_temp.is_(False)).all() if recent else []
    team = resolve_team(session, "api", name, cands)
    if team is None and kind == "club":
        team = resolve_team(session, "api", name, [t for t in competition_teams(session, comp) if not t.is_temp])
    if team is None:
        team = Team(name=name, kind=kind, country=comp.country, is_temp=True)
        session.add(team)
        session.flush()
        add_alias(session, team.id, "api", name)
    team.api_id = api_id
    session.flush()
    return team


def upsert_api_fixture(session, p: dict, comp: Competition) -> Match:
    home = _api_team(session, comp, p["home_name"], p["home_api_id"], p["season"])
    away = _api_team(session, comp, p["away_name"], p["away_api_id"], p["season"])
    m = session.query(Match).filter_by(api_fixture_id=p["api_fixture_id"]).one_or_none()
    if m is None:
        m = find_match(session, home.id, away.id, p["kickoff_utc"])
    if m is None and comp.kind == "club":
        # openfootball fikstüründen gelmiş, tarihi kaymış maç: ligde bu ikili sezonda bir kez oynar
        m = (session.query(Match)
             .filter_by(competition_id=comp.id, season=p["season"], home_team_id=home.id, away_team_id=away.id,
                        status="scheduled", api_fixture_id=None)
             .first())
    if m is None:
        m = Match(competition_id=comp.id, season=p["season"], home_team_id=home.id, away_team_id=away.id,
                  kickoff_utc=p["kickoff_utc"], stats={})
        session.add(m)
    m.api_fixture_id = p["api_fixture_id"]
    m.competition_id, m.season = comp.id, p["season"]
    m.home_team_id, m.away_team_id = home.id, away.id
    m.kickoff_utc = p["kickoff_utc"]
    m.status = p["status"]
    m.home_goals, m.away_goals = p["home_goals"], p["away_goals"]
    m.neutral = comp.code in NEUTRAL_COMPETITIONS
    m.unmatched = bool(home.is_temp or away.is_temp)
    session.flush()
    return m


def _comp_for(session, league_id: int) -> Competition | None:
    lg = COMPETITION_BY_API_ID.get(league_id)
    if lg is None or lg.kind == "hidden":
        return None
    return session.query(Competition).filter_by(code=lg.code).one()


def _ingest_fixture_items(session, items: list[dict], rep: IngestReport) -> list[Match]:
    out = []
    for it in items:
        p = parse_api_fixture(it)
        comp = _comp_for(session, p["league_id"])
        if comp is None:
            continue
        out.append(upsert_api_fixture(session, p, comp))
        rep.fixtures += 1
    session.commit()
    return out


def team_season_totals(session, team_id: int, season: int) -> tuple[int, int]:
    games = goals = 0
    for m in matches_of_team(session, team_id).filter(Match.season == season, Match.status == "finished"):
        games += 1
        goals += (m.home_goals if m.home_team_id == team_id else m.away_goals) or 0
    return games, goals


def _store_odds(session, items: list[dict], now: datetime, rep: IngestReport, allowed: set[int]) -> None:
    for it in items:
        fid, book, odds = parse_api_odds(it)
        if fid not in allowed:
            continue
        m = session.query(Match).filter_by(api_fixture_id=fid).one_or_none()
        if m is None or not odds:
            continue
        for market, value in odds.items():
            session.add(OddsQuote(match_id=m.id, bookmaker=book, market=market, odds=value, fetched_at=now,
                                  source="api"))
        rep.odds += 1
    session.commit()


def _store_injuries(session, items: list[dict], fixtures: dict[int, Match], rep: IngestReport) -> None:
    ids = [m.id for m in fixtures.values()]
    session.query(MissingPlayer).filter(MissingPlayer.match_id.in_(ids), MissingPlayer.source == "api").delete(
        synchronize_session=False)
    for it in items:
        d = parse_api_injury(it)
        m = fixtures.get(d["api_fixture_id"])
        if m is None:
            continue
        team = session.query(Team).filter_by(api_id=d["team_api_id"]).first()
        if team is None or team.id not in (m.home_team_id, m.away_team_id):
            continue
        session.add(MissingPlayer(match_id=m.id, team_id=team.id, player_name=d["player_name"],
                                  api_player_id=d["player_id"], reason=d["reason"], source="api", active=True))
        rep.missing += 1
    session.commit()


def player_lookup_budget(api) -> int:
    """Oyuncu istatistiği için harcanabilecek istek: oranlar ve eksikler alındıktan sonra kotada kalan,
    sonuçlandırma payı (API_RESERVE) düşülerek. Yoğun maç günlerinde kendiliğinden azalır, kota bitmez."""
    left = api_left(api)
    return max(0, min(MAX_PLAYER_LOOKUPS, left - API_RESERVE))


def api_left(api) -> int:
    """Bugün kalan istek (API'nin bildirdiği kalan hak dahil); bilinemiyorsa 0."""
    try:
        if hasattr(api, "remaining_today"):
            return int(api.remaining_today())
        return int(api.daily_limit) - int(api.used_today())
    except (AttributeError, TypeError, ValueError):
        return 0


def _fetch_player_stats(session, api, rep: IngestReport, now: datetime | None = None) -> None:
    q = (session.query(MissingPlayer, Match).join(Match, MissingPlayer.match_id == Match.id)
         .filter(MissingPlayer.source == "api", MissingPlayer.api_player_id.isnot(None),
                 MissingPlayer.active.is_(True)))
    if now is not None:
        q = q.filter(Match.kickoff_utc >= now)  # oynanmış maçların eksikleri artık tahmini değiştirmez
    rows = q.order_by(Match.kickoff_utc).all()  # önce en yakın maçlar
    # Bütçe istek sayısıdır (oyuncu sayısı değil): bir oyuncu reddedilen sezon + izin verilen sezon diye
    # 2 istek tutabilir. 21 Kasım'da oyuncu başına sayıldığı için sonuçlandırma payı da harcandı.
    budget = player_lookup_budget(api)
    used = 0
    allowed_for: dict[int, int] = {}  # planın kapattığı sezon -> izin verilen son sezon (bir kez öğrenilir)

    def share_used() -> bool:
        if used < budget:
            return False
        rep.fallbacks.append(f"oyuncu istatistikleri: günlük kota payı doldu ({used} istek), kalanlar sonraki güncellemede")
        return True

    for mp, m in rows:
        if session.get(PlayerSeasonStat, (mp.api_player_id, m.season)) is not None:
            continue
        if share_used():
            break
        team = session.get(Team, mp.team_id)
        season = allowed_for.get(m.season, m.season)
        approx = season != m.season
        try:
            used += 1
            item = api.player_season(mp.api_player_id, season)
        except QuotaExceeded:
            rep.fallbacks.append("oyuncu istatistikleri: kota doldu")
            return
        except ApiError as exc:
            allowed = None if approx else _allowed_season(str(exc))
            if allowed is None:
                rep.errors.append(f"oyuncu {mp.player_name}: {exc}")
                continue
            allowed_for[m.season] = allowed  # sonraki oyuncular reddedilecek isteği hiç atmaz
            if share_used():
                break
            try:
                used += 1
                item = api.player_season(mp.api_player_id, allowed)
                approx = True
            except QuotaExceeded:
                rep.fallbacks.append("oyuncu istatistikleri: kota doldu")
                return
            except ApiError as exc2:
                rep.errors.append(f"oyuncu {mp.player_name}: {exc2}")
                continue
        parsed = parse_api_player(item, team.api_id if team else None) if item else None
        if parsed is None:
            parsed = {"position": "M", "minutes": 0, "goals": 0, "assists": 0}
        if approx:
            games, goals = APPROX_TEAM_GAMES, APPROX_TEAM_GOALS
        else:
            games, goals = team_season_totals(session, mp.team_id, m.season)
        session.merge(PlayerSeasonStat(api_player_id=mp.api_player_id, season=m.season, team_games=games,
                                       team_goals=goals, **parsed))
        session.commit()


def _allowed_season(message: str) -> int | None:
    """Plan mesajından ("try from 2022 to 2024") izin verilen son sezonu çıkarır."""
    import re
    m = re.search(r"to (\d{4})", message)
    return int(m.group(1)) if m and "plan" in message.lower() else None


def ingest_upcoming(session, api, now: datetime, days: int = 2) -> IngestReport:
    rep = IngestReport()
    if api is None:
        rep.fallbacks.append("API anahtarı yok: yaklaşan maçlar, oranlar ve eksikler alınamadı")
        return rep
    local_today = now.astimezone(TZ).date()
    matches: list[Match] = []
    for d in (local_today + timedelta(days=i) for i in range(days)):
        try:
            matches += _ingest_fixture_items(session, api.fixtures_on(d), rep)
        except QuotaExceeded:
            rep.errors.append(f"fikstür: kota doldu ({d})")
            return rep
        except ApiError as exc:
            rep.errors.append(f"fikstür hatası {d}: {exc}")
    upcoming = [m for m in matches if m.status == "scheduled" and m.kickoff_utc > now]
    soon = now + timedelta(hours=48)
    recent = now - timedelta(hours=ODDS_REFRESH_HOURS)
    for m in sorted(upcoming, key=lambda x: x.kickoff_utc):
        if m.kickoff_utc > soon:
            continue
        fresh = session.query(OddsQuote).filter(OddsQuote.match_id == m.id, OddsQuote.source == "api",
                                                OddsQuote.fetched_at >= recent).first()
        if fresh is not None:
            continue
        try:
            _store_odds(session, api.odds_for_fixture(m.api_fixture_id), now, rep, {m.api_fixture_id})
        except QuotaExceeded:
            rep.fallbacks.append("oranlar: kota doldu, kalan maçlar için oran yok")
            return rep
        except ApiError as exc:
            rep.errors.append(f"oran hatası {m.api_fixture_id}: {exc}")
    days = sorted({m.kickoff_utc.date() for m in upcoming if m.kickoff_utc <= soon})
    for d in days:
        fixtures = {m.api_fixture_id: m for m in upcoming if m.kickoff_utc.date() == d}
        try:
            items = api.injuries_on(d)
        except QuotaExceeded:
            rep.fallbacks.append("eksikler: kota doldu")
            return rep
        except ApiError as exc:
            rep.errors.append(f"eksik hatası {d}: {exc}")
            continue
        _store_injuries(session, items, fixtures, rep)
    _fetch_player_stats(session, api, rep, now)
    return rep


LATE_ODDS_HOURS = 6  # maçına bu kadar saatten az kalan maçların oranı gün içinde yenilenir
LATE_ODDS_MIN_AGE = timedelta(hours=3)  # bundan yeni oranı olan maç atlanır
LATE_ODDS_RESERVE = 10  # günün kalan sonuçlandırma çalıştırmaları için bırakılan istek


def refresh_late_odds(session, api, now: datetime) -> list[int]:
    """Maça yakın oranları yeniler; oranı yenilenen maçların id'lerini döndürür.

    Maça yakın oran sabah oranından daha isabetlidir (DraftKings ölçümü: açılış logloss 0,976, kapanış 0,969);
    tahmin oranla birleştirildiği için bu, maç saatine yakın gösterilen ihtimali iyileştirir. Sadece günlük
    kotadan artan istekler kullanılır; otomatik oyun sabah kurulduğu için kuponları değiştirmez.
    """
    if api is None:
        return []
    budget = api_left(api) - LATE_ODDS_RESERVE
    matches = (session.query(Match)
               .filter(Match.status == "scheduled", Match.api_fixture_id.isnot(None), Match.unmatched.is_(False),
                       Match.kickoff_utc > now + timedelta(minutes=10),
                       Match.kickoff_utc <= now + timedelta(hours=LATE_ODDS_HOURS))
               .order_by(Match.kickoff_utc).all())
    rep, done = IngestReport(), []
    for m in matches:
        if len(done) >= budget:
            break
        fresh = session.query(OddsQuote).filter(OddsQuote.match_id == m.id, OddsQuote.source == "api",
                                                OddsQuote.fetched_at >= now - LATE_ODDS_MIN_AGE).first()
        if fresh is not None:
            continue
        try:
            items = api.odds_for_fixture(m.api_fixture_id, max_age=timedelta(hours=1))
        except QuotaExceeded:
            break
        except ApiError:
            continue
        before = rep.odds
        _store_odds(session, items, now, rep, {m.api_fixture_id})
        if rep.odds > before:
            done.append(m.id)
    return done


def ingest_results(session, api, now: datetime, fetch=fetch_text) -> IngestReport:
    rep = IngestReport()
    if api is None:
        rep.fallbacks.append("API yok: sonuçlar openfootball verisinden (gecikmeli)")
        today = now.astimezone(TZ).date()
        rep.results = ingest_openfootball(session, [current_season_start(today)], today=today, fetch=fetch, now=now)
        return rep
    for d in (now.date() - timedelta(days=1), now.date()):
        try:
            ms = _ingest_fixture_items(session, api.fixtures_on(d), rep)
            rep.results += sum(1 for m in ms if m.status == "finished")
        except QuotaExceeded:
            rep.errors.append("sonuçlar: kota doldu")
            break
        except ApiError as exc:
            rep.errors.append(f"sonuç hatası {d}: {exc}")
    return rep


def backfill_season(session, api, season: int, leagues=None) -> IngestReport:
    """Bir sezonun tüm fikstür ve sonuçlarını API'den doldurur (lig başına 1 istek)."""
    rep = IngestReport()
    for lg in leagues or CLUB_LEAGUES:
        try:
            items = api.fixtures_for_league(lg.api_id, season, timedelta(days=6))
            ms = _ingest_fixture_items(session, items, rep)
            rep.results += sum(1 for m in ms if m.status == "finished")
        except QuotaExceeded:
            rep.errors.append("geçmiş doldurma: kota doldu")
            break
        except ApiError as exc:
            rep.errors.append(f"{lg.name} {season}: {exc}")
    return rep


def backfill_dates(session, api, start: date, end: date, max_requests: int = 30) -> IngestReport:
    """Geçmiş günlerin sonuçlarını tarih tabanlı sorguyla tamamlar (ücretsiz planda çalışır).

    Her çağrıda en fazla `max_requests` yeni gün sorgular; tamamlanan günleri hatırlar.
    """
    from app.config import get_value, set_value

    rep = IngestReport()
    if get_value(session, "backfill_blocked"):
        return rep  # plan geçmiş günlere izin vermiyor; boşuna istek harcama
    done = set(get_value(session, "backfill_dates_done", []) or [])
    used = 0
    d = start
    while d <= end and used < max_requests:
        key = d.isoformat()
        if key not in done:
            try:
                ms = _ingest_fixture_items(session, api.fixtures_on_past(d), rep)
                rep.results += sum(1 for m in ms if m.status == "finished")
            except QuotaExceeded:
                rep.errors.append("geçmiş günler: kota doldu")
                break
            except ApiError as exc:
                rep.errors.append(f"geçmiş gün {key}: {exc}")
                if "plan" in str(exc).lower():
                    set_value(session, "backfill_blocked", {"at": key, "reason": str(exc)[:200]})
                break
            done.add(key)
            used += 1
            set_value(session, "backfill_dates_done", sorted(done))
        d += timedelta(days=1)
    return rep


def gap_start(session, today: date) -> date:
    """Bu sezon için yerel (openfootball) verinin bittiği gün; kulüp ligleri arasında en erken."""
    season = current_season_start(today)
    first = date(season, 7, 15)
    lasts = []
    for lg in CLUB_LEAGUES:
        comp = session.query(Competition).filter_by(code=lg.code).one()
        last = (session.query(Match.kickoff_utc)
                .filter(Match.competition_id == comp.id, Match.season == season, Match.status == "finished",
                        Match.api_fixture_id.is_(None))
                .order_by(Match.kickoff_utc.desc()).first())
        lasts.append(last[0].date() if last else first)
    return max(first, min(lasts))
