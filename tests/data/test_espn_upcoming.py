"""ESPN'den önümüzdeki maçlar, saatleri, oranları ve milli maçlar (API-Football olmadan da site dolu görünsün)."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.data import espn, espn_upcoming
from app.data.espn_upcoming import ingest_espn_upcoming
from app.models import Competition, Match, OddsQuote, Team

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)  # Türkiye saatiyle 27 Kasım 12:00
EMPTY = json.dumps({"leagues": [{"season": {"year": 2026}}], "events": []})


def _ev(eid, home, away, when, state="pre", name="STATUS_SCHEDULED", odds=None, score=None, season=2026):
    sides = [{"homeAway": "home", "team": {"displayName": home}, "score": str(score[0]) if score else "0"},
             {"homeAway": "away", "team": {"displayName": away}, "score": str(score[1]) if score else "0"}]
    return {"id": eid, "date": when, "season": {"year": season},
            "status": {"type": {"name": name, "state": state, "completed": state == "post"}},
            "competitions": [{"competitors": sides, "odds": odds if odds is not None else []}]}


def _dk(home, draw, away, over=None, under=None, line="2.5"):
    o = {"provider": {"name": "DraftKings"},
         "moneyline": {"home": {"close": {"odds": home}}, "draw": {"close": {"odds": draw}},
                       "away": {"close": {"odds": away}}}}
    if over:
        o["total"] = {"over": {"close": {"line": "o" + line, "odds": over}},
                      "under": {"close": {"line": "u" + line, "odds": under}}}
    return o


def _board(*events):
    return json.dumps({"leagues": [{"season": {"year": 2026}}], "events": list(events)})


def _summary_b365():
    team = {"preMatchFullTimeResultHome": {"value": "6/5"}, "preMatchFullTimeResultDraw": {"value": "5/2"},
            "preMatchFullTimeResultAway": {"value": "9/4"}, "preMatchDoubleChanceHomeOrDraw": {"value": "1/3"},
            "preMatchDoubleChanceHomeOrAway": {"value": "1/4"}, "preMatchDoubleChanceDrawOrAway": {"value": "4/9"},
            "preMatchOverUnderHandicap": {"value": "2.5"}, "preMatchGoalLineOver": {"value": "4/5"},
            "preMatchGoalLineUnder": {"value": "1/1"}}
    return json.dumps({"odds": [{"provider": {"name": "Bet 365"}, "bettingOdds": {"teamOdds": team}}]})


@pytest.fixture
def tmp_espn(tmp_path, monkeypatch):
    monkeypatch.setattr(espn, "ESPN_DIR", tmp_path)
    monkeypatch.setattr(espn_upcoming, "ESPN_DIR", tmp_path)
    return tmp_path


def _fetcher(pages, calls):
    """pages: {(slug, 'YYYYMMDD'): json} ve {('summary', id): json}; olmayan gün boş liste döner."""
    def fetch(url, dest, max_age):
        if "summary?event=" in url:
            key = ("summary", url.rsplit("=", 1)[1])
        else:
            key = (url.split("/soccer/")[1].split("/")[0], url.rsplit("=", 1)[1])
        calls.append(key)
        text = pages.get(key, EMPTY)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return text
    return fetch


def _club(session, *names, code="T1", season=2026):
    comp = session.query(Competition).filter_by(code=code).one()
    teams = {n: Team(name=n, kind="club", country="Türkiye") for n in names}
    filler = Team(name="Doldurma Kulübü", kind="club", country="Türkiye")
    session.add_all([*teams.values(), filler])
    session.flush()
    # takımlar bu sezonun fikstüründe olsun (ESPN adları yalnızca sezonun takımları arasında aranır);
    # ligde bir ikili sezonda bir kez oynar, bu yüzden bitmiş maçlar ayrı bir rakiple
    for t in teams.values():
        session.add(Match(competition_id=comp.id, season=season, kickoff_utc=datetime(2026, 8, 10, tzinfo=UTC),
                          home_team_id=t.id, away_team_id=filler.id, status="finished",
                          home_goals=1, away_goals=0, stats={}))
    session.commit()
    return comp, teams


def _quotes(session, match_id, source="espn"):
    rows = session.query(OddsQuote).filter_by(match_id=match_id, source=source).all()
    return {(q.bookmaker, q.market): q.odds for q in rows}


def test_planned_match_gets_espn_time_and_draftkings_odds(session, tmp_espn):
    comp, t = _club(session, "Beşiktaş", "Galatasaray")
    plan = Match(competition_id=comp.id, season=2026, kickoff_utc=datetime(2026, 11, 30, 17, 0, tzinfo=UTC),
                 home_team_id=t["Galatasaray"].id, away_team_id=t["Beşiktaş"].id, status="scheduled", stats={})
    session.add(plan)
    session.commit()
    ev = _ev("401", "Galatasaray", "Besiktas", "2026-11-28T18:00Z",
             odds=[_dk("+300", "+260", "-115", "-150", "+115")])
    rep = ingest_espn_upcoming(session, NOW, fetch=_fetcher({("tur.1", "20261128"): _board(ev)}, []),
                               api_is_live=False, summaries=False)
    session.refresh(plan)
    assert plan.kickoff_utc == datetime(2026, 11, 28, 18, 0, tzinfo=UTC)
    assert plan.stats["espn_id"] == "401"
    assert _quotes(session, plan.id) == {("DK", "MS1"): 4.0, ("DK", "MSX"): 3.6, ("DK", "MS2"): 1.87,
                                         ("DK", "UST25"): 1.667, ("DK", "ALT25"): 2.15}
    assert rep.odds == 1 and rep.unresolved == []


def test_missing_fixture_is_created(session, tmp_espn):
    comp, t = _club(session, "Gaziantep FK", "Amed SFK")
    ev = _ev("402", "Gaziantep", "Amed SFK", "2026-11-28T16:00Z", odds=[_dk("+120", "+230", "+210")])
    rep = ingest_espn_upcoming(session, NOW, fetch=_fetcher({("tur.1", "20261128"): _board(ev)}, []),
                               api_is_live=False, summaries=False)
    m = session.query(Match).filter_by(status="scheduled").one()
    assert (m.home_team_id, m.away_team_id, m.season) == (t["Gaziantep FK"].id, t["Amed SFK"].id, 2026)
    assert m.kickoff_utc == datetime(2026, 11, 28, 16, 0, tzinfo=UTC) and m.stats["espn_id"] == "402"
    assert rep.created == 1


def test_rerun_replaces_espn_odds_instead_of_piling_up(session, tmp_espn):
    _club(session, "Gaziantep FK", "Amed SFK")
    first = _board(_ev("402", "Gaziantep", "Amed SFK", "2026-11-28T16:00Z", odds=[_dk("+120", "+230", "+210")]))
    second = _board(_ev("402", "Gaziantep", "Amed SFK", "2026-11-28T16:00Z", odds=[_dk("+100", "+230", "+250")]))
    ingest_espn_upcoming(session, NOW, fetch=_fetcher({("tur.1", "20261128"): first}, []),
                         api_is_live=False, summaries=False)
    ingest_espn_upcoming(session, NOW + timedelta(hours=7), fetch=_fetcher({("tur.1", "20261128"): second}, []),
                         api_is_live=False, summaries=False)
    m = session.query(Match).filter_by(status="scheduled").one()
    assert _quotes(session, m.id) == {("DK", "MS1"): 2.0, ("DK", "MSX"): 3.3, ("DK", "MS2"): 3.5}


def test_api_kickoff_is_kept_while_api_answers(session, tmp_espn):
    comp, t = _club(session, "Beşiktaş", "Galatasaray")
    api_time = datetime(2026, 11, 28, 17, 0, tzinfo=UTC)
    m = Match(competition_id=comp.id, season=2026, kickoff_utc=api_time, home_team_id=t["Galatasaray"].id,
              away_team_id=t["Beşiktaş"].id, status="scheduled", api_fixture_id=555, stats={})
    session.add(m)
    session.commit()
    page = {("tur.1", "20261128"): _board(_ev("401", "Galatasaray", "Besiktas", "2026-11-28T18:00Z"))}
    ingest_espn_upcoming(session, NOW, fetch=_fetcher(page, []), api_is_live=True, summaries=False)
    session.refresh(m)
    assert m.kickoff_utc == api_time
    ingest_espn_upcoming(session, NOW, fetch=_fetcher(page, []), api_is_live=False, summaries=False)
    session.refresh(m)
    assert m.kickoff_utc == datetime(2026, 11, 28, 18, 0, tzinfo=UTC)


def test_unknown_team_is_reported_not_created(session, tmp_espn):
    _club(session, "Gaziantep FK", "Amed SFK")
    page = {("tur.1", "20261128"): _board(_ev("403", "Gaziantep", "Real Madrid", "2026-11-28T16:00Z"))}
    rep = ingest_espn_upcoming(session, NOW, fetch=_fetcher(page, []), api_is_live=False, summaries=False)
    assert session.query(Match).filter_by(status="scheduled").count() == 0
    assert rep.unresolved == ["Süper Lig 2026: Real Madrid"]
    assert session.query(Team).filter_by(name="Real Madrid").count() == 0


def test_postponed_match_is_marked(session, tmp_espn):
    comp, t = _club(session, "Gaziantep FK", "Amed SFK")
    page = {("tur.1", "20261128"): _board(_ev("402", "Gaziantep", "Amed SFK", "2026-11-28T16:00Z",
                                              state="post", name="STATUS_POSTPONED"))}
    ingest_espn_upcoming(session, NOW, fetch=_fetcher(page, []), api_is_live=False, summaries=False)
    assert session.query(Match).filter_by(home_team_id=t["Gaziantep FK"].id,
                                          away_team_id=t["Amed SFK"].id).filter(
        Match.kickoff_utc > NOW).one().status == "postponed"


def test_empty_odds_entry_is_ignored(session, tmp_espn):
    """Süper Lig'de bazı maçların oran listesi [null] geliyor."""
    _club(session, "Samsunspor", "Gençlerbirliği")
    page = {("tur.1", "20261128"): _board(_ev("404", "Samsunspor", "Genclerbirligi", "2026-11-28T13:00Z",
                                              odds=[None]))}
    rep = ingest_espn_upcoming(session, NOW, fetch=_fetcher(page, []), api_is_live=False, summaries=False)
    assert rep.errors == [] and session.query(OddsQuote).count() == 0
    assert session.query(Match).filter_by(status="scheduled").count() == 1


def test_match_summary_adds_bet365_double_chance(session, tmp_espn):
    _club(session, "Gaziantep FK", "Amed SFK")
    pages = {("tur.1", "20261128"): _board(_ev("402", "Gaziantep", "Amed SFK", "2026-11-28T16:00Z",
                                               odds=[_dk("+120", "+230", "+210")])),
             ("summary", "402"): _summary_b365()}
    calls = []
    ingest_espn_upcoming(session, NOW, fetch=_fetcher(pages, calls), api_is_live=False)
    m = session.query(Match).filter_by(status="scheduled").one()
    q = _quotes(session, m.id)
    assert {k: v for k, v in q.items() if k[0] == "B365"} == {
        ("B365", "MS1"): 2.2, ("B365", "MSX"): 3.5, ("B365", "MS2"): 3.25, ("B365", "CS1X"): 1.333,
        ("B365", "CS12"): 1.25, ("B365", "CSX2"): 1.444, ("B365", "UST25"): 1.8, ("B365", "ALT25"): 2.0}
    assert ("summary", "402") in calls


def test_summaries_are_not_fetched_while_api_answers(session, tmp_espn):
    _club(session, "Gaziantep FK", "Amed SFK")
    pages = {("tur.1", "20261128"): _board(_ev("402", "Gaziantep", "Amed SFK", "2026-11-28T16:00Z")),
             ("summary", "402"): _summary_b365()}
    calls = []
    ingest_espn_upcoming(session, NOW, fetch=_fetcher(pages, calls), api_is_live=True)
    assert not [c for c in calls if c[0] == "summary"]


def test_national_fixture_with_odds_and_fresh_result(session, tmp_espn):
    nat = {n: Team(name=n, kind="national", country=n) for n in ("Norway", "Wales", "Georgia", "Hungary")}
    session.add_all(nat.values())
    session.commit()
    pages = {("uefa.nations", "20261128"): _board(_ev("501", "Norway", "Wales", "2026-11-28T19:45Z",
                                                      odds=[_dk("-200", "+330", "+500")])),
             ("uefa.nations", "20261126"): _board(_ev("502", "Georgia", "Hungary", "2026-11-26T17:00Z",
                                                      state="post", name="STATUS_FULL_TIME", score=(0, 2)))}
    rep = ingest_espn_upcoming(session, NOW, fetch=_fetcher(pages, []), api_is_live=False, summaries=False)
    unl = session.query(Competition).filter_by(code="UNL").one()
    fixture = session.query(Match).filter_by(competition_id=unl.id, status="scheduled").one()
    assert (fixture.home_team_id, fixture.away_team_id) == (nat["Norway"].id, nat["Wales"].id)
    assert _quotes(session, fixture.id)[("DK", "MS1")] == 1.5
    done = session.query(Match).filter_by(competition_id=unl.id, status="finished").one()
    assert (done.home_team_id, done.home_goals, done.away_goals) == (nat["Georgia"].id, 0, 2)
    assert rep.results == 1
