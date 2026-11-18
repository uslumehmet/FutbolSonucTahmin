import json

import httpx
import pytest

from app.data.api_football import ApiError, ApiFootball, QuotaExceeded


def _transport(calls, body=None, status=200, headers=None):
    def handler(request: httpx.Request):
        calls.append(request)
        payload = body(request) if callable(body) else (body or {"errors": [], "response": [{"ok": 1}], "paging": {"current": 1, "total": 1}})
        return httpx.Response(status, json=payload, headers=headers or {})
    return httpx.MockTransport(handler)


def test_cached_response_not_counted(session):
    calls = []
    api = ApiFootball(session, "KEY", transport=_transport(calls))
    from datetime import timedelta
    a = api.get("/status", {"x": 1}, timedelta(hours=1))
    b = api.get("/status", {"x": 1}, timedelta(hours=1))
    assert a == b and len(calls) == 1 and api.used_today() == 1


def test_quota_exceeded(session):
    calls = []
    from datetime import timedelta
    api = ApiFootball(session, "KEY", daily_limit=1, transport=_transport(calls))
    api.get("/a", {}, timedelta(hours=1))
    with pytest.raises(QuotaExceeded):
        api.get("/b", {}, timedelta(hours=1))
    assert len(calls) == 1


def test_sends_key_header(session):
    calls = []
    from datetime import timedelta
    ApiFootball(session, "SECRET", transport=_transport(calls)).get("/a", {}, timedelta(hours=1))
    assert calls[0].headers["x-apisports-key"] == "SECRET"


def test_api_errors_raise(session):
    calls = []
    from datetime import timedelta
    body = {"errors": {"plan": "Free plans do not have access to this season, try from 2022 to 2024."}, "response": []}
    api = ApiFootball(session, "KEY", transport=_transport(calls, body=body))
    with pytest.raises(ApiError, match="season"):
        api.get("/fixtures", {"league": 39, "season": 2026}, timedelta(hours=1))


def test_rate_limited_response_raises_quota(session):
    calls = []
    from datetime import timedelta
    api = ApiFootball(session, "KEY", transport=_transport(calls, status=429, body={"errors": {"rateLimit": "Too many requests"}}))
    with pytest.raises(QuotaExceeded):
        api.get("/a", {}, timedelta(hours=1))


def test_get_all_pages(session):
    calls = []
    from datetime import timedelta

    def body(req):
        page = int(req.url.params.get("page", 1))
        return {"errors": [], "response": [{"p": page}], "paging": {"current": page, "total": 2}}

    api = ApiFootball(session, "KEY", transport=_transport(calls, body=body))
    assert api.get_all_pages("/odds", {"league": 39}, timedelta(hours=1)) == [{"p": 1}, {"p": 2}]
    assert len(calls) == 2


def test_month_bytes(session):
    calls = []
    from datetime import timedelta
    api = ApiFootball(session, "KEY", transport=_transport(calls))
    api.get("/a", {}, timedelta(hours=1))
    assert api.month_bytes() > 0


def test_odds_for_fixture_asks_bet365_first_then_all(session):
    from datetime import timedelta  # noqa: F401
    calls = []

    def body(req):
        if req.url.params.get("bookmaker") == "8":
            return {"errors": [], "response": [], "paging": {"current": 1, "total": 1}}
        return {"errors": [], "response": [{"fixture": {"id": 1}, "bookmakers": []}], "paging": {"current": 1, "total": 1}}

    api = ApiFootball(session, "KEY", transport=_transport(calls, body=body), sleeper=lambda s: None)
    out = api.odds_for_fixture(1)
    assert len(out) == 1
    assert [dict(c.url.params) for c in calls] == [{"fixture": "1", "bookmaker": "8"}, {"fixture": "1"}]
