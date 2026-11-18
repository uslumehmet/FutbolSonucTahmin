"""Bilgisayardaki SQLite veritabanını bulut veritabanına (PostgreSQL) kopyalar."""
from __future__ import annotations

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine

from app.db import init_db
from app.models import Base, Match

CHUNK = 2000


class TargetNotEmpty(RuntimeError):
    pass


def _has_rows(engine: Engine, table) -> bool:
    if not inspect(engine).has_table(table.name):
        return False
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar() > 0


def copy_database(src: Engine, dst: Engine, force: bool = False, progress=print) -> dict[str, int]:
    """Bütün tabloları src'den dst'ye kopyalar. dst'de maç varsa force olmadan durur (yanlışlıkla silmesin)."""
    init_db(src)  # eski şemada eksik sütun varsa tamamlanır
    init_db(dst)
    if not force and _has_rows(dst, Match.__table__):
        raise TargetNotEmpty("Hedef veritabanında zaten maç var. Üzerine yazmak için --zorla kullanın.")
    tables = Base.metadata.sorted_tables
    counts: dict[str, int] = {}
    with dst.begin() as out:
        for table in reversed(tables):  # önce bağımlı tablolar silinir
            out.execute(table.delete())
        for table in tables:
            n = 0
            with src.connect() as inp:
                result = inp.execution_options(stream_results=True).execute(select(table))
                while rows := result.mappings().fetchmany(CHUNK):
                    out.execute(table.insert(), [dict(r) for r in rows])
                    n += len(rows)
            counts[table.name] = n
            progress(f"  {table.name}: {n} satır")
        if dst.dialect.name == "postgresql":
            _reset_sequences(out, tables)
    return counts


def _reset_sequences(conn, tables) -> None:
    """Kopyalanan satırların id'leri elle yazıldı; yeni kayıtlar en büyük id'den devam etsin."""
    for table in tables:
        pk = list(table.primary_key.columns)
        if len(pk) != 1 or pk[0].name != "id":
            continue
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM \"{table.name}\"), 1), "
            f"(SELECT MAX(id) FROM \"{table.name}\") IS NOT NULL)"))
