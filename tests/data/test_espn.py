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
    assert events[0] == {"season": 2026, "kickoff_utc": datetime(2026, 10, 18, 16, 0, tzinfo=UTC), "home": "Kasimpasa",
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
