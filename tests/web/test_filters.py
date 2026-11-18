from datetime import UTC, datetime

from app.web.main import local_day, local_dt, tl


def test_local_day_never_contains_time():
    assert local_day(datetime(2026, 12, 7, 15, 30, tzinfo=UTC)) == "Pzt 7 Ara"
    assert local_day(datetime(2026, 11, 17, 15, 30, tzinfo=UTC)) == "Sal 17 Kas"


def test_local_dt_and_tl():
    assert local_dt(datetime(2026, 11, 16, 15, 30, tzinfo=UTC)) == "Pzt 16 Kas 18:30"
    assert tl(123_450) == "1.234,50 ₺" and tl(100_000) == "1.000 ₺" and tl(-300) == "-3 ₺"
