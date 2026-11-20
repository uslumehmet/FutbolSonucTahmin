import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.data.api_football import ApiFootball
from app.data.api_parse import parse_api_fixture, parse_api_odds
from app.data.ingest_api import ingest_results, ingest_upcoming, refresh_late_odds
from app.data.teams import get_or_create_team
from app.models import ApiCall, Competition, Match, MissingPlayer, OddsQuote, PlayerSeasonStat, Team

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 21, 8, 0, tzinfo=UTC)


def load(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def _seed_teams(session):
    """Kanonik (openfootball) takımlar + geçmiş maç; lig adayları olsunlar."""
    for code, names in {"E0": ["Arsenal FC", "Everton FC"], "SP1": ["FC Barcelona", "Sevilla FC"],
                        "T1": ["Galatasaray", "Fenerbahçe"]}.items():
        comp = session.query(Competition).filter_by(code=code).one()
        a, b = (get_or_create_team(session, "of", n, "club") for n in names)
        session.add(Match(competition_id=comp.id, season=2025, kickoff_utc=datetime(2026, 3, 1, 15, tzinfo=UTC),
                          home_team_id=a.id, away_team_id=b.id, status="finished", home_goals=1, away_goals=0, stats={}))
    session.commit()


PLAN_ERR = {"errors": {"plan": "Free plans do not have access to this season, try from 2022 to 2024."}, "response": []}


def make_api(session, fail_odds_for=(), calls=None, player_plan_block=False):
    def handler(req: httpx.Request):
        path = req.url.path
        if calls is not None:
            calls.append((path, dict(req.url.params)))
        if path.endswith("/fixtures"):
            if "season" in req.url.params:
                return httpx.Response(200, json=PLAN_ERR)
            return httpx.Response(200, json=load("api_fixtures.json"))
        if path.endswith("/odds"):
            if "season" in req.url.params:
                return httpx.Response(200, json=PLAN_ERR)
            if req.url.params.get("fixture") in fail_odds_for:
                return httpx.Response(429, json={"errors": {"rateLimit": "x"}})
            return httpx.Response(200, json=load("api_odds.json"))
        if path.endswith("/injuries"):
            if "season" in req.url.params:
                return httpx.Response(200, json=PLAN_ERR)
            return httpx.Response(200, json=load("api_injuries.json"))
        if path.endswith("/players"):
            if player_plan_block and req.url.params.get("season") != "2024":
                return httpx.Response(200, json=PLAN_ERR)
            return httpx.Response(200, json=load("api_players.json"))
        return httpx.Response(200, json={"errors": [], "response": []})

    return ApiFootball(session, "KEY", transport=httpx.MockTransport(handler), clock=lambda: NOW)


def test_parse_fixture_uses_90_minute_score():
    item = {"fixture": {"id": 1, "date": "2026-11-21T15:00:00+00:00", "status": {"short": "AET"}},
            "league": {"id": 5, "season": 2026},
            "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
            "goals": {"home": 2, "away": 1},
            "score": {"fulltime": {"home": 1, "away": 1}, "extratime": {"home": 1, "away": 0}}}
    p = parse_api_fixture(item)
    assert (p["status"], p["home_goals"], p["away_goals"]) == ("finished", 1, 1)


@pytest.mark.parametrize("short,status", [("PST", "postponed"), ("CANC", "cancelled"), ("ABD", "abandoned"),
                                          ("AWD", "abandoned"), ("NS", "scheduled"), ("2H", "scheduled"),
                                          ("FT", "finished")])
def test_parse_fixture_status_mapping(short, status):
    item = load("api_fixtures.json")["response"][0]
    item["fixture"]["status"]["short"] = short
    item["score"]["fulltime"] = {"home": 1, "away": 0}
    assert parse_api_fixture(item)["status"] == status


def test_parse_odds_maps_markets():
    fid, book, odds = parse_api_odds(load("api_odds.json")["response"][0])
    assert (fid, book) == (9001, "B365")
    assert odds == {"MS1": 1.40, "MSX": 4.80, "MS2": 8.00, "UST25": 1.70, "ALT25": 2.10, "KGVAR": 1.80,
                    "KGYOK": 1.95, "CS1X": 1.08, "CS12": 1.20, "CSX2": 2.75, "UST15": 1.20}


def test_parse_odds_averages_without_bet365():
    _fid, book, odds = parse_api_odds(load("api_odds.json")["response"][1])
    assert book == "AVG" and odds["MS1"] == pytest.approx(1.32)


def test_ingest_upcoming_creates_matches_odds_and_missing(session):
    _seed_teams(session)
    rep = ingest_upcoming(session, make_api(session), NOW, days=1)
    ars = session.query(Match).filter_by(api_fixture_id=9001).one()
    assert ars.status == "scheduled" and ars.unmatched is False
    assert ars.home_team.name == "Arsenal FC"
    assert session.query(Match).filter_by(api_fixture_id=9004).count() == 0
    assert session.query(OddsQuote).filter_by(match_id=ars.id, source="api", market="MS1").one().odds == 1.40
    miss = session.query(MissingPlayer).filter_by(match_id=ars.id, source="api").all()
    assert {m.player_name for m in miss} == {"B. Saka", "I. Gueye"}
    assert session.get(PlayerSeasonStat, (1460, 2026)).position == "F"
    assert rep.fixtures >= 3 and rep.odds >= 1


def test_unresolved_team_flags_match(session):
    _seed_teams(session)
    ingest_upcoming(session, make_api(session), NOW, days=1)
    m = session.query(Match).filter_by(api_fixture_id=9002).one()
    assert m.unmatched is True
    assert session.get(Team, m.away_team_id).is_temp is True
    assert session.get(Team, m.home_team_id).name == "Galatasaray"


def test_quota_midrun_keeps_partial_data(session):
    _seed_teams(session)
    rep = ingest_upcoming(session, make_api(session, fail_odds_for=("9002",)), NOW, days=1)
    assert session.query(Match).filter_by(api_fixture_id=9001).count() == 1
    assert any("kota" in f for f in rep.fallbacks + rep.errors)


def test_manual_missing_survives_refresh(session):
    _seed_teams(session)
    api = make_api(session)
    ingest_upcoming(session, api, NOW, days=1)
    m = session.query(Match).filter_by(api_fixture_id=9001).one()
    session.add(MissingPlayer(match_id=m.id, team_id=m.home_team_id, player_name="Elle", source="manual",
                              role="hücum", level="orta", attack_effect=0.05, concede_effect=0.0))
    session.commit()
    session.query(ApiCall).delete()
    session.commit()
    ingest_upcoming(session, api, NOW, days=1)
    names = {x.player_name for x in session.query(MissingPlayer).filter_by(match_id=m.id)}
    assert names == {"B. Saka", "I. Gueye", "Elle"}


def test_ingest_results_updates_score(session):
    _seed_teams(session)
    ingest_results(session, make_api(session), NOW)
    m = session.query(Match).filter_by(api_fixture_id=9003).one()
    assert (m.status, m.home_goals, m.away_goals) == ("finished", 2, 1)
    assert m.home_team.name == "FC Barcelona"


def test_no_api_reports_fallback(session):
    rep = ingest_upcoming(session, None, NOW)
    assert rep.fallbacks and rep.fixtures == 0


def test_odds_fetched_per_fixture_and_not_refetched(session):
    _seed_teams(session)
    calls = []
    api = make_api(session, calls=calls)
    ingest_upcoming(session, api, NOW, days=1)
    odds_calls = [p for path, p in calls if path.endswith("/odds")]
    assert sorted(c["fixture"] for c in odds_calls) == ["9001", "9002"]
    assert all("season" not in c for c in odds_calls)
    session.query(ApiCall).delete()
    session.commit()
    calls.clear()
    ingest_upcoming(session, api, NOW, days=1)
    assert not [p for path, p in calls if path.endswith("/odds")]


def test_injuries_fetched_by_date(session):
    _seed_teams(session)
    calls = []
    ingest_upcoming(session, make_api(session, calls=calls), NOW, days=1)
    inj = [p for path, p in calls if path.endswith("/injuries")]
    assert inj == [{"date": "2026-11-21"}]


def test_player_stats_fall_back_to_allowed_season(session):
    _seed_teams(session)
    ingest_upcoming(session, make_api(session, player_plan_block=True), NOW, days=1)
    st = session.get(PlayerSeasonStat, (1460, 2026))
    assert st is not None and st.minutes == 900 and st.team_games == 38 and st.team_goals == 53


def test_backfill_dates_fills_gap_within_budget(session):
    from datetime import date
    from app.data.ingest_api import backfill_dates
    _seed_teams(session)
    calls = []
    api = make_api(session, calls=calls)
    rep = backfill_dates(session, api, date(2026, 11, 10), date(2026, 11, 20), max_requests=3)
    fixture_dates = [p["date"] for path, p in calls if path.endswith("/fixtures")]
    assert fixture_dates == ["2026-11-10", "2026-11-11", "2026-11-12"]
    rep2 = backfill_dates(session, api, date(2026, 11, 10), date(2026, 11, 20), max_requests=3)
    assert [p["date"] for path, p in calls if path.endswith("/fixtures")][3:] == ["2026-11-13", "2026-11-14", "2026-11-15"]
    assert rep.fixtures > 0 and rep2.errors == []


def test_parse_odds_maps_goal_lines_and_team_totals():
    item = {"fixture": {"id": 5}, "bookmakers": [{"id": 8, "name": "Bet365", "bets": [
        {"name": "Goals Over/Under", "values": [{"value": "Over 1.5", "odd": "1.25"}, {"value": "Under 1.5", "odd": "3.75"},
                                                {"value": "Over 3.5", "odd": "2.60"}, {"value": "Under 3.5", "odd": "1.45"}]},
        {"name": "Total - Home", "values": [{"value": "Over 0.5", "odd": "1.20"}, {"value": "Under 0.5", "odd": "4.20"},
                                            {"value": "Over 1.5", "odd": "2.00"}, {"value": "Under 1.5", "odd": "1.75"}]},
        {"name": "Total - Away", "values": [{"value": "Over 0.5", "odd": "1.50"}, {"value": "Under 0.5", "odd": "2.50"},
                                            {"value": "Over 1.5", "odd": "3.40"}, {"value": "Under 1.5", "odd": "1.30"}]},
        {"name": "Goals Over/Under First Half", "values": [{"value": "Over 1.5", "odd": "9.99"}]},
    ]}]}
    _fid, _book, odds = parse_api_odds(item)
    assert odds == {"UST15": 1.25, "ALT15": 3.75, "UST35": 2.60, "ALT35": 1.45, "EVUST05": 1.20, "EVALT05": 4.20,
                    "EVUST15": 2.00, "EVALT15": 1.75, "DEPUST05": 1.50, "DEPALT05": 2.50, "DEPUST15": 3.40,
                    "DEPALT15": 1.30}


def test_api_fixture_merges_into_openfootball_schedule_even_if_moved(session):
    """openfootball'dan gelen ileri tarihli maç, API'den gelince çift kayıt olmaz (tarih 3 gün kaymış olsa da)."""
    from datetime import timedelta

    _seed_teams(session)
    e0 = session.query(Competition).filter_by(code="E0").one()
    ars = session.query(Team).filter_by(name="Arsenal FC").one()
    eve = session.query(Team).filter_by(name="Everton FC").one()
    planned = Match(competition_id=e0.id, season=2026, home_team_id=ars.id, away_team_id=eve.id, status="scheduled",
                    kickoff_utc=datetime(2026, 11, 21, 15, tzinfo=UTC) - timedelta(days=3), stats={})
    session.add(planned)
    session.commit()
    ingest_upcoming(session, make_api(session), NOW, days=1)
    m = session.query(Match).filter_by(api_fixture_id=9001).one()
    assert m.id == planned.id
    assert m.kickoff_utc == datetime(2026, 11, 21, 15, tzinfo=UTC)
    assert session.query(Match).filter_by(competition_id=e0.id, season=2026).count() == 1


def test_player_lookup_budget_leaves_reserve():
    from app.data.ingest_api import API_RESERVE, MAX_PLAYER_LOOKUPS, player_lookup_budget

    class Api:
        daily_limit = 100

        def __init__(self, used):
            self.used = used

        def used_today(self):
            return self.used

    assert player_lookup_budget(Api(10)) == MAX_PLAYER_LOOKUPS  # sakin gün
    assert player_lookup_budget(Api(80)) == 100 - 80 - API_RESERVE  # yoğun gün: sonuçlandırma payı kalır
    assert player_lookup_budget(Api(95)) == 0
    assert player_lookup_budget(object()) == 0


def test_player_stats_skip_when_quota_share_used(session):
    _seed_teams(session)
    api = make_api(session)
    api.daily_limit = 3 + 15  # fikstür + oran + eksik isteklerinden sonra oyuncuya pay kalmaz
    rep = ingest_upcoming(session, api, NOW, days=1)
    assert session.get(PlayerSeasonStat, (1460, 2026)) is None
    assert any("kota payı" in f for f in rep.fallbacks)


def test_refresh_late_odds_near_kickoff_within_quota(session):
    _seed_teams(session)
    calls = []
    api = make_api(session, calls=calls)
    ingest_upcoming(session, api, NOW, days=1)  # 08:00 UTC; maçlar 15:00 ve 16:00
    assert refresh_late_odds(session, api, NOW) == []  # 7 saatten fazla var
    later = NOW + timedelta(hours=4)
    api.clock = lambda: later
    calls.clear()
    ids = refresh_late_odds(session, api, later)
    ars = session.query(Match).filter_by(api_fixture_id=9001).one()
    assert ids == [ars.id]  # eşleşmeyen takımın maçı (tahmini yok) atlanır
    assert len([p for path, p in calls if path.endswith("/odds")]) == 1
    latest = session.query(OddsQuote).filter_by(market="MS1").order_by(OddsQuote.fetched_at.desc()).first()
    assert latest.fetched_at == later
    calls.clear()
    assert refresh_late_odds(session, api, later) == [] and not calls  # oranlar artık taze
    api.clock = lambda: later + timedelta(hours=1, minutes=30)
    api.daily_limit = api.used_today() + 10  # artan kota yok: sonuçlandırma payı korunur
    assert refresh_late_odds(session, api, later + timedelta(hours=1, minutes=30)) == []
