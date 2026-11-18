"""Önbellekli dosya indirme."""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import httpx

USER_AGENT = "futbol-sonuc-tahmin/1.0 (kisisel kullanim)"


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def is_fresh(dest: Path, max_age: timedelta | None) -> bool:
    if not dest.exists():
        return False
    if max_age is None:
        return True
    return (time.time() - dest.stat().st_mtime) < max_age.total_seconds()


def fetch_text(url: str, dest: Path, max_age: timedelta | None) -> str:
    """Dosya diskte ve tazeyse onu döndürür; değilse indirir ve kaydeder.

    max_age=None: dosya varsa asla yeniden indirilmez (bitmiş sezonlar).
    """
    if is_fresh(dest, max_age):
        return _decode(dest.read_bytes())
    resp = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return _decode(resp.content)
