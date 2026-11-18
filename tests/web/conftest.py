import pytest
from fastapi.testclient import TestClient

from app.betting.wallets import ensure_default_wallets
from app.model.predict import predict_upcoming
from app.model.store import ModelStore
from app.web.main import create_app
from tests.helpers import NOW, seed_synthetic_league


@pytest.fixture
def client(session_factory):
    app = create_app(session_factory, start_scheduler=False, clock=lambda: NOW)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(session_factory):
    with session_factory() as s:
        ids = seed_synthetic_league(s, comp_code="E0", upcoming=5)
        predict_upcoming(s, ModelStore(s), NOW)
        ensure_default_wallets(s)
    return ids


@pytest.fixture
def client_seeded(session_factory, seeded):
    app = create_app(session_factory, start_scheduler=False, clock=lambda: NOW)
    with TestClient(app) as c:
        c.match_ids = seeded
        yield c
