from datetime import UTC, date, datetime, timedelta

import httpx

from app.config import get_value
from app.data.api_football import ApiFootball
from app.data.ingest_api import backfill_dates

PER_MIN = {"errors": {"rateLimit": "Too many requests. You have reached your per-minute request limit. Please wait a few seconds before retrying."}, "response": []}
OK = {"errors": [], "response": [{"ok": 1}], "paging": {"current": 1, "total": 1}}


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += timedelta(seconds=seconds)


def test_throttles_to_per_minute_limit(session):
    clk = FakeClock()
    api = ApiFootball(session, "K", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=OK)),
                      clock=clk.now, sleeper=clk.sleep, per_minute=10)
    for i in range(11):
        api.get("/x", {"i": i}, timedelta(hours=1))
    assert len(clk.slept) == 1 and 55 <= clk.slept[0] <= 61


def test_retries_once_after_per_minute_429(session):
    clk = FakeClock()
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        return httpx.Response(429, json=PER_MIN) if state["n"] == 1 else httpx.Response(200, json=OK)

    api = ApiFootball(session, "K", transport=httpx.MockTransport(handler), clock=clk.now, sleeper=clk.sleep)
    assert api.get("/x", {}, timedelta(hours=1))["response"] == [{"ok": 1}]
    assert state["n"] == 2 and clk.slept and api.used_today() == 1


def test_backfill_marks_plan_block_and_stops(session):
    plan = {"errors": {"plan": "Free plans do not have access to this date, try from 2026-11-15 to 2026-11-17."}, "response": []}
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=plan)

    api = ApiFootball(session, "K", transport=httpx.MockTransport(handler), sleeper=lambda s: None)
    rep = backfill_dates(session, api, date(2026, 9, 7), date(2026, 11, 15), max_requests=30)
    assert len(calls) == 1 and rep.errors
    assert get_value(session, "backfill_blocked")
    backfill_dates(session, api, date(2026, 9, 7), date(2026, 11, 15), max_requests=30)
    assert len(calls) == 1
