from datetime import UTC, datetime
from pathlib import Path

from app.data.ingest import ingest_international
from app.data.international import parse_international, tournament_tier
from app.models import Competition, Match

SAMPLE = (Path(__file__).parent / "fixtures" / "intl_sample.csv").read_text(encoding="utf-8")


def test_tiers():
    assert tournament_tier("FIFA World Cup") == "WC"
    assert tournament_tier("UEFA Nations League") == "UNL"
    assert tournament_tier("FIFA World Cup qualification") == "WCQ"
    assert tournament_tier("UEFA Euro qualification") == "EUROQ"
    assert tournament_tier("African Cup of Nations qualification") == "QUAL"
    assert tournament_tier("Copa América") == "CONT"
    assert tournament_tier("Friendly") == "FRIENDLY"
    assert tournament_tier("Kirin Cup") == "OTHER"


def test_parse_drops_unplayed_and_pre1990():
    df = parse_international(SAMPLE)
    assert len(df) == 5
    assert "Greece" not in set(df.away)
    assert df.iloc[0].kickoff_utc == datetime(2022, 11, 20, 12, 0, tzinfo=UTC)


def test_neutral_parsed():
    df = parse_international(SAMPLE)
    assert bool(df.query("home == 'Argentina'").iloc[0].neutral) is True
    assert bool(df.query("home == 'Qatar'").iloc[0].neutral) is False


def test_ingest_international_idempotent(session):
    fake = lambda url, dest, max_age: SAMPLE  # noqa: E731
    assert ingest_international(session, fetch=fake) == 5
    assert ingest_international(session, fetch=fake) == 5
    hist = session.query(Competition).filter_by(code="INTL_HIST").one()
    ms = session.query(Match).filter_by(competition_id=hist.id).all()
    assert len(ms) == 5
    assert {m.stats["tier"] for m in ms} == {"WC", "UNL", "QUAL", "CONT"}
    assert all(m.status == "finished" for m in ms)
