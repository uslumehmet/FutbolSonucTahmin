import os

import pytest

from app.config import normalize_db_url
from app.db import init_db, make_engine, make_session_factory
from app.models import Base

# İsteğe bağlı: testleri gerçek PostgreSQL'de çalıştırmak için TEST_DB_URL verilir.
# Her testte tablolar silinip yeniden kurulur; bu yüzden adında "test" geçmeyen veritabanı kabul edilmez.
TEST_DB_URL = os.environ.get("TEST_DB_URL", "").strip()
if TEST_DB_URL and "test" not in TEST_DB_URL.rsplit("/", 1)[-1].split("?")[0]:
    raise RuntimeError("TEST_DB_URL'deki veritabanı adında 'test' geçmeli (asıl veritabanı silinmesin).")


@pytest.fixture
def engine():
    if TEST_DB_URL:
        eng = make_engine(normalize_db_url(TEST_DB_URL))
        Base.metadata.drop_all(eng)
    else:
        eng = make_engine("sqlite://")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _no_real_api_key(monkeypatch):
    """Testler kullanıcının .env içindeki gerçek API anahtarından ve çalıştığı ortamdan etkilenmesin."""
    for name in ("API_FOOTBALL_KEY", "BAHIS_NO_API", "GITHUB_ACTIONS", "RENDER"):
        monkeypatch.delenv(name, raising=False)
