from datetime import UTC, datetime

from app.web.main import local_day, local_dt, tl


def test_local_day_never_contains_time():
    assert local_day(datetime(2026, 12, 7, 15, 30, tzinfo=UTC)) == "Pzt 7 Ara"
    assert local_day(datetime(2026, 11, 17, 15, 30, tzinfo=UTC)) == "Sal 17 Kas"


def test_local_dt_and_tl():
    assert local_dt(datetime(2026, 11, 16, 15, 30, tzinfo=UTC)) == "Pzt 16 Kas 18:30"
    assert tl(123_450) == "1.234,50 ₺" and tl(100_000) == "1.000 ₺" and tl(-300) == "-3 ₺"


def test_local_iso():
    from app.web.main import local_iso
    assert local_iso("2026-11-16T06:07:00+00:00") == "Pzt 16 Kas 09:07"
    assert local_iso("2026-11-16T06:07:00") == "Pzt 16 Kas 09:07"
