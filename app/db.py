"""Veritabanı bağlantısı ve ilk kurulum."""
from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import ALL_COMPETITIONS, DATA_DIR
from app.models import Base, Competition


def make_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        # PostgreSQL (Neon): bulut veritabanı boşta uyuyabilir; kopan bağlantı sessizce yenilenir.
        # prepare_threshold=None: Neon'un bağlantı havuzlayıcısı (PgBouncer) ile de sorunsuz çalışır.
        return create_engine(url, pool_pre_ping=True, pool_recycle=300, pool_size=3, max_overflow=2,
                             connect_args={"prepare_threshold": None})
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url in ("sqlite://", "sqlite:///:memory:"):
        kwargs["poolclass"] = StaticPool
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - bağlantı ayarı
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Basit şema güncellemesi: tabloda olmayan sütunları ekler (ALTER TABLE ADD COLUMN; SQLite ve PostgreSQL)."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    with sessionmaker(engine)() as s:
        existing = {c.code: c for c in s.query(Competition)}
        for lg in ALL_COMPETITIONS:
            row = existing.get(lg.code)
            if row is None:
                s.add(Competition(code=lg.code, name=lg.name, country=lg.country, kind=lg.kind, api_id=lg.api_id))
            else:
                row.name, row.country, row.kind, row.api_id = lg.name, lg.country, lg.kind, lg.api_id
        s.commit()


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
