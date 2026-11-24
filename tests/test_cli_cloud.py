"""Bilgisayardaki görev: `python -m app.cli bulut-guncelle` buluttaki veritabanına bağlanıp zamanı gelen işleri yapar."""
from app import cli, jobs


def _record(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "catch_up", lambda sf, now, settle=True: calls.append(settle) or [])
    return calls


def test_refuses_without_a_cloud_address(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("HEDEF_DB_URL", raising=False)
    calls = _record(monkeypatch)
    cli.main(["bulut-guncelle"])
    assert calls == [] and "HEDEF_DB_URL" in capsys.readouterr().out


def test_refuses_a_local_database(monkeypatch, capsys):
    """Yanlışlıkla SQLite'a yazıp sitede hiçbir şey görünmemesin."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HEDEF_DB_URL", "sqlite:///baska.db")
    calls = _record(monkeypatch)
    cli.main(["bulut-guncelle"])
    assert calls == [] and "PostgreSQL" in capsys.readouterr().out


def test_cloud_address_comes_from_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HEDEF_DB_URL", "postgres://u:p@host/db?sslmode=require")
    assert cli.cloud_db_url() == "postgresql+psycopg://u:p@host/db?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h2/db2")
    assert cli.cloud_db_url() == "postgresql+psycopg://a:b@h2/db2"
