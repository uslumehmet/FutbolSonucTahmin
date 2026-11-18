from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from app.config import CLUB_LEAGUES
from app.data.ingest import ingest_openfootball
from app.data.openfootball import json_url, parse_of_json, parse_of_txt, txt_url
from app.models import Competition, Match, Team

FIX = Path(__file__).parent / "fixtures"
EN = (FIX / "of_en1.json").read_text(encoding="utf-8")
TR25 = (FIX / "of_tr1_2025.txt").read_text(encoding="utf-8")
TR24 = (FIX / "of_tr1_2024.txt").read_text(encoding="utf-8")
LG = {lg.code: lg for lg in CLUB_LEAGUES}


def test_urls():
    assert json_url("E0", 2025) == "https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json"
    assert txt_url(2026) == "https://raw.githubusercontent.com/openfootball/europe/master/turkey/2026-27_tr1.txt"


def test_parse_json_played_only_and_local_time():
    df = parse_of_json(EN, "Europe/London")
    assert len(df) == 3
    assert df.query("home == 'Chelsea FC'").iloc[0].kickoff_utc == datetime(2025, 8, 16, 14, 0, tzinfo=UTC)
    liv = df.query("home == 'Liverpool FC'").iloc[0]
    assert (liv.fthg, liv.ftag) == (3, 0)
    assert liv.kickoff_utc == datetime(2025, 12, 21, 15, 0, tzinfo=UTC)


def test_parse_txt_inherits_time_and_infers_year():
    df = parse_of_txt(TR25, "Europe/Istanbul", 2025)
    assert len(df) == 4
    kas = df.query("home == 'Kasimpasa'").iloc[0]
    assert kas.kickoff_utc == datetime(2025, 8, 16, 16, 0, tzinfo=UTC)
    assert (kas.fthg, kas.ftag, kas.away) == (1, 1, "Trabzonspor")
    fb = df.query("home == 'Fenerbahçe'").iloc[0]
    assert fb.kickoff_utc == datetime(2026, 1, 17, 17, 0, tzinfo=UTC)
    assert fb.away == "Beşiktaş"
    assert df.query("home == 'Galatasaray'").iloc[0].away == "Çorum"


def _fake(url, dest, max_age):
    if url.endswith("2025-26_tr1.txt"):
        return TR25
    if url.endswith("2024-25_tr1.txt"):
        return TR24
    if url.endswith("en.1.json") and "2025-26" in url:
        return EN
    raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))


def test_ingest_openfootball_idempotent(session):
    n1 = ingest_openfootball(session, [2025], today=date(2026, 1, 20), fetch=_fake, leagues=[LG["E0"], LG["T1"]])
    n2 = ingest_openfootball(session, [2025], today=date(2026, 1, 20), fetch=_fake, leagues=[LG["E0"], LG["T1"]])
    assert n1 == 7 and n2 == 7
    assert session.query(Match).count() == 7
    m = session.query(Match).filter(Match.home_goals == 3, Match.away_goals == 0).one()
    assert m.status == "finished" and m.season == 2025


def test_missing_season_is_skipped(session):
    assert ingest_openfootball(session, [2021], today=date(2026, 1, 20), fetch=_fake, leagues=[LG["T1"]]) == 0


def test_sponsor_name_change_maps_to_same_team(session):
    ingest_openfootball(session, [2024, 2025], today=date(2026, 1, 20), fetch=_fake, leagues=[LG["T1"]])
    konya = [t for t in session.query(Team) if "konyaspor" in t.name.lower()]
    assert len(konya) == 1
    t1 = session.query(Competition).filter_by(code="T1").one()
    assert session.query(Match).filter_by(competition_id=t1.id, home_team_id=konya[0].id).count() == 2


def test_parse_txt_format_with_v_separator():
    df = parse_of_txt((FIX / "of_tr1_formatb.txt").read_text(encoding="utf-8"), "Europe/Istanbul", 2025)
    assert len(df) == 4
    gs = df.query("away == 'Galatasaray'").iloc[0]
    assert (gs.home, gs.fthg, gs.ftag) == ("Gaziantep FK", 0, 3)
    assert gs.kickoff_utc == datetime(2025, 8, 8, 17, 30, tzinfo=UTC)
    ant = df.query("home == 'Antalyaspor'").iloc[0]
    assert ant.away == "Kasımpaşa SK" and ant.kickoff_utc == datetime(2025, 8, 9, 15, 0, tzinfo=UTC)
    assert df.query("home == 'Fenerbahçe'").iloc[0].kickoff_utc == datetime(2026, 1, 18, 16, 0, tzinfo=UTC)


def test_parse_txt_july_date_uses_weekday_for_year():
    text = "= X\n\n▪ 30. Round\n  Fri Jul 3\n    20:30  Galatasaray   v Konyaspor   2-0 (1-0)\n"
    df = parse_of_txt(text, "Europe/Istanbul", 2019)
    assert df.iloc[0].kickoff_utc.date() == date(2020, 7, 3)


def test_names_in_same_season_never_merge(session):
    import json as _json
    text = _json.dumps({"matches": [
        {"date": "2014-08-24", "time": "18:00", "team1": "Espanyol Barcelona", "team2": "Sevilla FC", "score": {"ft": [1, 0]}},
        {"date": "2014-08-24", "time": "20:00", "team1": "FC Barcelona", "team2": "Elche CF", "score": {"ft": [3, 0]}},
        {"date": "2014-09-24", "time": "20:00", "team1": "FC Barcelona", "team2": "Espanyol Barcelona", "score": {"ft": [5, 1]}},
    ]})
    n = ingest_openfootball(session, [2014], today=date(2026, 1, 20), fetch=lambda u, d, m: text, leagues=[LG["SP1"]])
    assert n == 3
    assert session.query(Match).count() == 3
    assert session.query(Team).count() == 4


# --- ileri tarihli (henüz oynanmamış) fikstürler ---
from datetime import timedelta  # noqa: E402

from app.data.openfootball import parse_of_json_fixtures, parse_of_txt_fixtures  # noqa: E402


def test_parse_json_fixtures_returns_only_unplayed():
    df = parse_of_json_fixtures(EN, "Europe/London")
    assert list(zip(df.home, df.away)) == [("Tottenham Hotspur FC", "Fulham FC")]
    assert df.iloc[0].kickoff_utc == datetime(2025, 12, 27, 15, 0, tzinfo=UTC)


def test_parse_txt_fixtures_returns_only_unplayed_and_inherits_time():
    df = parse_of_txt_fixtures(TR25, "Europe/Istanbul", 2025)
    assert list(zip(df.home, df.away)) == [("Samsunspor", "Göztepe")]
    assert df.iloc[0].kickoff_utc == datetime(2026, 1, 17, 17, 0, tzinfo=UTC)
    fb = parse_of_txt_fixtures((FIX / "of_tr1_formatb.txt").read_text(encoding="utf-8"), "Europe/Istanbul", 2025)
    assert list(zip(fb.home, fb.away)) == [("Beşiktaş", "Göztepe")]
    assert fb.iloc[0].kickoff_utc == datetime(2026, 1, 18, 16, 0, tzinfo=UTC)


def test_played_parsers_unchanged_by_fixture_lines():
    assert len(parse_of_txt(TR25, "Europe/Istanbul", 2025)) == 4
    assert len(parse_of_json(EN, "Europe/London")) == 3


def test_ingest_adds_future_fixtures_of_current_season_as_scheduled(session):
    for _ in range(2):  # iki kez çalışsa da tek kayıt
        n = ingest_openfootball(session, [2025], today=date(2025, 12, 1), fetch=_fake, leagues=[LG["E0"]])
        assert n == 3
    m = session.query(Match).filter_by(status="scheduled").one()
    assert m.home_team.name == "Tottenham Hotspur FC" and m.home_goals is None and m.api_fixture_id is None
    assert m.kickoff_utc == datetime(2025, 12, 27, 15, 0, tzinfo=UTC)


def test_ingest_skips_unplayed_fixtures_already_in_the_past(session):
    ingest_openfootball(session, [2025], today=date(2026, 1, 20), fetch=_fake, leagues=[LG["E0"]])
    assert session.query(Match).filter_by(status="scheduled").count() == 0


def test_scheduled_fixture_becomes_finished_when_score_arrives(session):
    ingest_openfootball(session, [2025], today=date(2025, 12, 1), fetch=_fake, leagues=[LG["E0"]])
    sid = session.query(Match).filter_by(status="scheduled").one().id
    played = EN.replace('"team2": "Fulham FC"}', '"team2": "Fulham FC", "score": {"ft": [2, 2]}}')
    ingest_openfootball(session, [2025], today=date(2026, 1, 20), fetch=lambda u, d, a: played, leagues=[LG["E0"]])
    m = session.get(Match, sid)
    assert (m.status, m.home_goals, m.away_goals) == ("finished", 2, 2)
    assert session.query(Match).count() == 4
