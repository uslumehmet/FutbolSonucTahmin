import json
from datetime import UTC, date, datetime

import pytest

from app.config import get_value
from app.data import espn
from app.data.espn import ingest_espn, parse_scoreboard
from app.models import Competition, Match, Team

TODAY = date(2026, 11, 20)


def _event(home, away, hs, as_, when="2026-10-18T16:00Z", done=True, season=None):
    return {"id": "1", "date": when, "season": {"year": season or int(when[:4]) - (int(when[5:7]) < 7)},
            "status": {"type": {"completed": done, "state": "post" if done else "pre"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": str(hs)},
                {"homeAway": "away", "team": {"displayName": away}, "score": str(as_)}]}]}


def _board(season, calendar, events):
    return json.dumps({"leagues": [{"season": {"year": season}, "calendar": calendar}], "events": events})


def test_parse_scoreboard():
    text = _board(2026, ["2026-10-18T07:00Z", "2026-10-19T07:00Z"],
                  [_event("Kasimpasa", "Samsunspor", 4, 1), _event("Goztepe", "Kocaelispor", 0, 0, done=False)])
    season, cal, events = parse_scoreboard(text)
    assert season == 2026 and cal == [date(2026, 10, 18), date(2026, 10, 19)]
    assert events[0] == {"espn_id": "1", "season": 2026, "kickoff_utc": datetime(2026, 10, 18, 16, 0, tzinfo=UTC), "home": "Kasimpasa",
                         "away": "Samsunspor", "completed": True, "fthg": 4, "ftag": 1}
    assert events[1]["completed"] is False and events[1]["fthg"] is None


@pytest.fixture
def tmp_espn(tmp_path, monkeypatch):
    monkeypatch.setattr(espn, "ESPN_DIR", tmp_path)
    return tmp_path


def _fake(pages, calls):
    """pages: {YYYYMMDD: json}. Takvim isteği (15 Ekim) için '1015' ile biten anahtar kullanılır."""
    def fetch(url, dest, max_age):
        key = url.rsplit("=", 1)[1]
        calls.append(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(pages[key], encoding="utf-8")
        return pages[key]
    return fetch


def _setup(session):
    comp = session.query(Competition).filter_by(code="T1").one()
    teams = {n: Team(name=n, kind="club", country="Türkiye") for n in
             ("Kasımpaşa SK", "Samsunspor", "Göztepe", "Kocaelispor")}
    session.add_all(teams.values())
    session.flush()
    t = teams
    # bu sezonun openfootball fikstürü (henüz sonuçsuz) ve API'den gelmiş bitmiş bir maç
    plan = Match(competition_id=comp.id, season=2026, kickoff_utc=datetime(2026, 10, 18, 13, 0, tzinfo=UTC),
                 home_team_id=t["Kasımpaşa SK"].id, away_team_id=t["Samsunspor"].id, status="scheduled", stats={})
    api = Match(competition_id=comp.id, season=2026, kickoff_utc=datetime(2026, 10, 19, 16, 0, tzinfo=UTC),
                home_team_id=t["Göztepe"].id, away_team_id=t["Kocaelispor"].id, status="finished",
                home_goals=2, away_goals=2, api_fixture_id=99, stats={})
    session.add_all([plan, api])
    session.commit()
    return comp, t, plan, api


def test_ingest_fills_planned_match_and_keeps_api_result(session, tmp_espn):
    comp, t, plan, api = _setup(session)
    cal = ["2026-10-18T07:00Z", "2026-10-19T07:00Z", "2026-11-25T07:00Z"]
    pages = {"20261015": _board(2026, cal, []),
             "20261018": _board(2026, cal, [_event("Kasimpasa", "Samsunspor", 4, 1)]),
             "20261019": _board(2026, cal, [_event("Goztepe", "Kocaelispor", 9, 9, when="2026-10-19T16:00Z")])}
    calls = []
    rep = ingest_espn(session, TODAY, codes=["T1"], fetch=_fake(pages, calls))
    assert rep.errors == [] and rep.unresolved == []
    session.refresh(plan)
    session.refresh(api)
    assert (plan.status, plan.home_goals, plan.away_goals) == ("finished", 4, 1)
    assert (api.home_goals, api.away_goals) == (2, 2)  # API sonucu esas
    assert "20261125" not in calls  # gelecekteki gün istenmez
    assert session.query(Team).count() == 4  # yeni takım açılmadı
    assert "T1:20261018" in get_value(session, "espn_done")
    calls.clear()
    ingest_espn(session, TODAY, codes=["T1"], fetch=_fake(pages, calls))
    assert calls == ["20261015"]  # işlenmiş günler bir daha indirilmez (takvim günde bir yenilenir)


def test_unknown_name_in_known_season_is_not_created(session, tmp_espn):
    _setup(session)
    cal = ["2026-10-18T07:00Z"]
    pages = {"20261015": _board(2026, cal, []),
             "20261018": _board(2026, cal, [_event("Kasimpasa", "Galatasaray", 1, 1)])}
    rep = ingest_espn(session, TODAY, codes=["T1"], fetch=_fake(pages, []))
    assert rep.unresolved == ["Süper Lig 2026: Galatasaray"]
    assert session.query(Team).filter_by(name="Galatasaray").count() == 0
    assert "T1:20261018" not in (get_value(session, "espn_done") or [])  # eşleşme düzelince yeniden işlenir


def test_missing_season_creates_teams(session, tmp_espn):
    cal = ["2021-10-17T07:00Z"]
    pages = {"20211015": _board(2021, cal, []),
             "20211017": _board(2021, cal, [_event("Altay Izmir", "Giresunspor", 0, 1, when="2021-10-17T13:00Z")])}
    rep = ingest_espn(session, TODAY, codes=["T1"], seasons=[2021], fetch=_fake(pages, []))
    assert rep.results == 1
    m = session.query(Match).filter_by(season=2021).one()
    assert (m.home_goals, m.away_goals, m.status) == (0, 1, "finished")


def test_next_season_days_in_calendar_are_skipped(session, tmp_espn):
    cal = ["2021-10-17T07:00Z", "2022-08-06T07:00Z"]
    pages = {"20211015": _board(2021, cal, []),
             "20211017": _board(2021, cal, [_event("Altay Izmir", "Giresunspor", 0, 1, when="2021-10-17T13:00Z")]),
             "20220806": _board(2021, cal, [_event("Sivasspor", "Besiktas", 2, 2, when="2022-08-06T17:00Z")])}
    ingest_espn(session, TODAY, codes=["T1"], seasons=[2021], fetch=_fake(pages, []))
    assert session.query(Match).filter_by(season=2021).count() == 1


def test_request_limit_stops_and_resumes(session, tmp_espn):
    _setup(session)
    cal = ["2026-10-18T07:00Z", "2026-10-19T07:00Z"]
    pages = {"20261015": _board(2026, cal, []),
             "20261018": _board(2026, cal, [_event("Kasimpasa", "Samsunspor", 4, 1)]),
             "20261019": _board(2026, cal, [])}
    rep = ingest_espn(session, TODAY, codes=["T1"], fetch=_fake(pages, []), max_requests=2)
    assert rep.stopped and rep.requests == 2
    rep = ingest_espn(session, TODAY, codes=["T1"], fetch=_fake(pages, []), max_requests=2)
    assert not rep.stopped and "T1:20261019" in get_value(session, "espn_done")


def _summary(home_id="1", away_id="2", stats=True, xi=11):
    def team(tid, shots, sot):
        return {"team": {"id": tid}, "statistics": [{"name": "totalShots", "displayValue": str(shots)},
                                                     {"name": "shotsOnTarget", "displayValue": str(sot)},
                                                     {"name": "possessionPct", "displayValue": "55.5"}] if stats else []}
    def roster(side, tid):
        return {"homeAway": side, "team": {"id": tid}, "roster": [
            {"starter": i < xi, "athlete": {"id": f"{tid}{i}", "displayName": f"P{i}"}, "position": {"abbreviation": "M"}}
            for i in range(16)]}
    return json.dumps({"header": {"competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": home_id}}, {"homeAway": "away", "team": {"id": away_id}}]}]},
        "boxscore": {"teams": [team(away_id, 9, 4), team(home_id, 24, 6)]},
        "rosters": [roster("home", home_id), roster("away", away_id)]})


def test_parse_summary_stats_and_lineups():
    from app.data.espn import parse_summary
    out = parse_summary(_summary())
    assert out["shots"] == [24, 9] and out["sot"] == [6, 4]  # takım sırası karışık gelse de ev/deplasman doğru
    assert len(out["xi_home"]) == 11 and out["xi_home"][0] == ["10", "P0", "M"]
    assert parse_summary(_summary(stats=False, xi=0)) == {"summary": "yok"}


def test_ingest_summaries_fills_stats_once(session, tmp_espn):
    from app.data.espn import ingest_summaries
    comp, t, plan, api = _setup(session)
    api.stats = {"espn_id": "777"}
    session.commit()
    calls = []

    def fetch(url, dest, max_age):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_summary(), encoding="utf-8")
        return _summary()

    rep = ingest_summaries(session, fetch=fetch, workers=2)
    session.refresh(api)
    assert rep.matches == 1 and rep.with_stats == 1 and rep.with_lineups == 1
    assert api.stats["sot"] == [6, 4] and api.stats["summary"] == "ok" and api.stats["espn_id"] == "777"
    assert ingest_summaries(session, fetch=fetch).matches == 0 and len(calls) == 1  # bir daha istenmez


ODDS = [{"provider": {"name": "DraftKings"}, "links": ["x"] * 50,
         "moneyline": {"home": {"close": {"odds": "+400"}, "open": {"odds": "+390"}},
                       "away": {"close": {"odds": "-160"}, "open": {"odds": "-165"}},
                       "draw": {"close": {"odds": "+320"}, "open": {"odds": "+310"}}},
         "total": {"over": {"close": {"line": "o2.5", "odds": "-160"}}, "under": {"close": {"line": "u2.5", "odds": "+130"}}}},
        {"provider": {"name": "Bet 365"}, "bettingOdds": {"teamOdds": {
            "preMatchFullTimeResultHome": {"value": "17/4"}, "preMatchFullTimeResultDraw": {"value": "31/10"},
            "preMatchFullTimeResultAway": {"value": "8/13"}, "preMatchDoubleChanceHomeOrDraw": {"value": "5/4"},
            "preMatchOverUnderHandicap": {"value": "3"}, "preMatchGoalLineOver": {"value": "39/40"},
            "liveFullTimeResultHome": {"value": "1/200"}}}}]


def test_parse_odds_converts_to_decimal_and_skips_whole_lines():
    from app.data.espn import parse_odds
    out = parse_odds({"odds": ODDS})
    assert out["odds_dk"]["close"] == {"MS1": 5.0, "MSX": 4.2, "MS2": 1.625, "UST25": 1.625, "ALT25": 2.3}
    assert out["odds_dk"]["open"] == {"MS1": 4.9, "MSX": 4.1, "MS2": 1.606}
    # Bet365: maç öncesi oranlar; canlı oran ve tam sayılı (iadeli) alt/üst çizgisi alınmaz
    assert out["odds_b365"] == {"MS1": 5.25, "MSX": 4.1, "MS2": 1.615, "CS1X": 2.25}


def test_slim_summary_keeps_what_parse_needs():
    from app.data.espn import parse_summary, slim_summary
    full = json.loads(_summary())
    full["odds"] = ODDS
    full["commentary"] = ["uzun metin"] * 1000
    text = json.dumps(full)
    slim = slim_summary(text)
    assert len(slim) < len(text) / 3
    assert parse_summary(slim) == parse_summary(text)
