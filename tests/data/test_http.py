import os
import time
from datetime import timedelta

import httpx

from app.data import http as http_mod


def test_fresh_file_not_redownloaded(tmp_path, monkeypatch):
    dest = tmp_path / "x.csv"
    dest.write_text("cached", encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("HTTP çağrılmamalı")

    monkeypatch.setattr(httpx, "get", boom)
    assert http_mod.fetch_text("https://example.invalid/x.csv", dest, None) == "cached"


def test_stale_file_is_redownloaded(tmp_path, monkeypatch):
    dest = tmp_path / "x.csv"
    dest.write_text("old", encoding="utf-8")
    old = time.time() - 10 * 86400
    os.utime(dest, (old, old))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, content=b"new", request=httpx.Request("GET", a[0])))
    assert http_mod.fetch_text("https://example.invalid/x.csv", dest, timedelta(days=3)) == "new"
