"""Bulut kurulumu: veritabanı adresi, model saklama, SQLite -> bulut taşıma, zamanlanmış işler."""
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app import jobs
from app.config import TZ, get_value, normalize_db_url, resolve_db_url, set_value
from app.db import init_db, make_engine
from app.migrate import TargetNotEmpty, copy_database
from app.model.layer2 import load_layer2_db, save_layer2_db
from app.models import JobRun, Match, SettingRow, Team
from tests.helpers import seed_synthetic_league


def test_normalize_db_url():
    assert normalize_db_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_db_url("postgresql://u:p@h/db?sslmode=require") == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_resolve_db_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BAHIS_DB_URL", raising=False)
    assert resolve_db_url().startswith("sqlite:///")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    assert resolve_db_url() == "postgresql+psycopg://u:p@h/db"


def test_postgres_engine_is_built_without_sqlite_options():
    eng = make_engine("postgresql+psycopg://u:p@localhost/db")  # bağlanmaz, sadece kurar
    assert eng.dialect.name == "postgresql"
    eng.dispose()


def test_layer2_roundtrip_in_db(session):
    assert load_layer2_db(session) is None
    save_layer2_db(session, {"1X2": None, "OU25": None})
    save_layer2_db(session, {"1X2": None, "OU25": None, "BTTS": None})  # ikinci kayıt üzerine yazar
    assert load_layer2_db(session) == {"1X2": None, "OU25": None, "BTTS": None}


def _file_engine(path):
    eng = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(eng)
    return eng


def test_copy_database(tmp_path):
    src, dst = _file_engine(tmp_path / "a.db"), _file_engine(tmp_path / "b.db")
    with sessionmaker(src)() as s:
        seed_synthetic_league(s, comp_code="E0", n_seasons=1, upcoming=3)
        s.add(SettingRow(key="x", value={"a": [1, 2]}))
        s.commit()
        n_match, n_team = s.query(Match).count(), s.query(Team).count()
        kickoff = s.query(Match).order_by(Match.id).first().kickoff_utc
    counts = copy_database(src, dst, progress=lambda _m: None)
    assert counts["match"] == n_match
    with sessionmaker(dst)() as s:
        assert s.query(Match).count() == n_match and s.query(Team).count() == n_team
        assert s.get(SettingRow, "x").value == {"a": [1, 2]}
        assert s.query(Match).order_by(Match.id).first().kickoff_utc == kickoff
    with pytest.raises(TargetNotEmpty):  # dolu hedefin üzerine kendiliğinden yazmaz
        copy_database(src, dst, progress=lambda _m: None)
    assert copy_database(src, dst, force=True, progress=lambda _m: None)["match"] == n_match
    src.dispose()
    dst.dispose()


def _stub(monkeypatch):
    ran = []
    for name in ("daily_update", "daily_autoplay", "settle_job", "refresh_sources", "weekly_retrain"):
        monkeypatch.setattr(jobs, name, lambda sf, now, _n=name: ran.append(_n))
    return ran


def _done(sf, job, at):
    with sf() as s:
        s.add(JobRun(job=job, run_key=at.astimezone(TZ).date().isoformat(), finished_at=at))
        s.commit()


def test_one_shot_run_at_night_updates_and_plays(session_factory, monkeypatch):
    """Bilgisayar gece açılırsa (tek seferlik çalıştırma) güncelleme yapılır, kupon hemen ardından kurulur;
    oranlar kuponla aynı anda alındığı için bayat kalmaz. Sabah 06:00'dan sonra güncelleme yeniden yapılır."""
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 18, 0, 37, tzinfo=TZ), settle=True)
    assert ran.index("settle_job") < ran.index("daily_update") < ran.index("daily_autoplay")
    with session_factory() as s:  # stub iş kaydı bırakmaz; gerçek güncelleme bırakır
        s.add(JobRun(job="update", run_key="2026-11-18", finished_at=datetime(2026, 11, 18, 0, 37, tzinfo=TZ)))
        s.commit()
    ran.clear()
    jobs.catch_up(session_factory, datetime(2026, 11, 18, 9, 7, tzinfo=TZ), settle=True)
    assert "daily_update" in ran


def test_periodic_check_at_night_waits_for_morning(session_factory, monkeypatch):
    """Sürekli açık yerel sunucunun 30 dakikalık kontrolü gece güncelleme ve kupon yapmaz (sabahı bekler)."""
    ran = _stub(monkeypatch)
    jobs.catch_up(session_factory, datetime(2026, 11, 18, 0, 37, tzinfo=TZ), settle=False)
    assert "daily_update" not in ran and "daily_autoplay" not in ran


def test_second_autoplay_call_keeps_first_report(session_factory):
    now = datetime(2026, 11, 18, 11, 5, tzinfo=TZ)
    with session_factory() as s:
        set_value(s, "last_autoplay", {"coupons": 4, "at": now.isoformat()})
    _done(session_factory, "autoplay", now)
    jobs.daily_autoplay(session_factory, now)
    with session_factory() as s:
        assert get_value(s, "last_autoplay")["coupons"] == 4


def test_prune_old_bodies_keeps_counters(session):
    from datetime import UTC, timedelta

    from app.data.api_football import ApiFootball, prune_old_bodies
    from app.models import ApiCall

    now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    old = ApiCall(endpoint="/odds", params_key="/odds?fixture=1", called_at=now - timedelta(days=11),
                  status_code=200, bytes=500, body='{"response": [1]}')
    new = ApiCall(endpoint="/odds", params_key="/odds?fixture=2", called_at=now - timedelta(days=1),
                  status_code=200, bytes=500, body='{"response": [2]}')
    session.add_all([old, new])
    session.commit()
    assert prune_old_bodies(session, now) == 1
    assert session.query(ApiCall).count() == 2  # istek kaydı kalır
    api = ApiFootball(session, "x", clock=lambda: now)
    assert api.cached("/odds", {"fixture": 1}, timedelta(days=3650)) is None  # temizlenen kayıt önbellek sayılmaz
    assert api.cached("/odds", {"fixture": 2}, timedelta(days=3)) == {"response": [2]}
