from datetime import date

from app.config import current_season_start, get_settings, save_settings, season_code
from app.models import Competition


def test_init_db_seeds_competitions(session):
    codes = {c.code for c in session.query(Competition)}
    assert codes == {"T1", "E0", "SP1", "I1", "D1", "F1", "WC", "WCQ_EU", "EURO", "EUROQ", "UNL", "INTL_HIST"}


def test_settings_roundtrip(session):
    save_settings(session, safe_min_odds=1.25)
    s = get_settings(session)
    assert s.safe_min_odds == 1.25
    assert s.value_min_prob == 0.40
    assert s.autoplay_targets == (2.0, 3.0, 5.0)


def test_season_helpers():
    assert season_code(2025) == "2526"
    assert current_season_start(date(2026, 11, 16)) == 2026
    assert current_season_start(date(2027, 3, 1)) == 2026


def test_init_db_adds_missing_columns(tmp_path):
    import sqlite3

    from app.db import init_db, make_engine

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE team (id INTEGER PRIMARY KEY, name VARCHAR(80), kind VARCHAR(16), country VARCHAR(40), is_temp BOOLEAN)")
    con.commit()
    con.close()
    eng = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(eng)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(team)")}
    assert "api_id" in cols
