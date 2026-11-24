"""API-Football hesabı askıya alınınca ya da anahtar geçersizken boşuna istek atılmaması; API durumunun kaydı."""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.data.api_football import ApiBlocked, ApiError, ApiFootball, api_live, api_status

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
SUSPENDED = {"errors": {"access": "Your account is suspended, check on https://dashboard.api-football.com."},
             "response": []}
BAD_KEY = {"errors": {"token": "Error/Missing application key. Go to https://www.api-football.com/documentation-v3"},
           "response": []}
OK = {"errors": [], "response": [], "paging": {"current": 1, "total": 1}}


def _api(session, bodies, calls, clock=lambda: NOW):
    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json=bodies[min(len(calls), len(bodies)) - 1])
    return ApiFootball(session, "KEY", transport=httpx.MockTransport(handler), clock=clock, sleeper=lambda s: None)


def test_suspended_account_stops_further_requests(session):
    calls = []
    api = _api(session, [SUSPENDED], calls)
    with pytest.raises(ApiBlocked, match="suspended"):
        api.fixtures_on(date(2026, 11, 24))
    with pytest.raises(ApiBlocked):
        api.fixtures_on(date(2026, 11, 25))
    assert len(calls) == 1
    st = api_status(session)
    assert st["ok"] is False and "suspended" in st["message"]


def test_invalid_key_blocks_too(session):
    calls = []
    api = _api(session, [BAD_KEY], calls)
    with pytest.raises(ApiBlocked):
        api.fixtures_on(date(2026, 11, 24))
    with pytest.raises(ApiBlocked):
        api.odds_for_fixture(1)
    assert len(calls) == 1


def test_blocked_error_is_an_api_error(session):
    """Mevcut `except ApiError` döngüleri askıda da çalışmaya devam eder (ama istek atmaz)."""
    assert issubclass(ApiBlocked, ApiError)


def test_plan_error_does_not_block(session):
    calls = []
    plan = {"errors": {"plan": "Free plans do not have access to this season, try from 2022 to 2024."}, "response": []}
    api = _api(session, [plan, OK], calls)
    with pytest.raises(ApiError) as exc:
        api.fixtures_on(date(2026, 11, 24))
    assert not isinstance(exc.value, ApiBlocked)
    api.fixtures_on(date(2026, 11, 25))
    assert len(calls) == 2


def test_successful_answer_makes_api_live_for_36_hours(session):
    calls = []
    _api(session, [OK], calls).fixtures_on(date(2026, 11, 24))
    assert api_live(session, NOW + timedelta(hours=35))
    assert not api_live(session, NOW + timedelta(hours=37))


def test_suspension_after_success_ends_live(session):
    calls = []
    _api(session, [OK], calls).fixtures_on(date(2026, 11, 24))
    later = NOW + timedelta(hours=2)
    with pytest.raises(ApiBlocked):
        _api(session, [SUSPENDED], [], clock=lambda: later).fixtures_on(date(2026, 11, 25))
    assert not api_live(session, later)
    assert api_status(session)["last_ok"] == NOW.isoformat()


def test_never_answered_is_not_live(session):
    assert not api_live(session, NOW)


def test_daily_ingest_stops_at_first_account_error(session):
    from app.data.ingest_api import ingest_results, ingest_upcoming

    calls = []
    api = _api(session, [SUSPENDED], calls)
    rep = ingest_upcoming(session, api, NOW)
    res = ingest_results(session, api, NOW)
    assert len(calls) == 1
    assert any("askıda" in f or "geçersiz" in f for f in rep.fallbacks)
    assert res.errors  # sonuçlandırma ESPN yedeğine geçsin diye hata olarak bildirilir
