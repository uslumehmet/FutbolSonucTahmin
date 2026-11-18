from datetime import date

import pytest

from app.betting.stats import live_calibration, mode_stats, singles_stats
from app.models import Coupon, Match, Prediction, SinglePick
from tests.helpers import NOW, seed_synthetic_league


def _coupon(session, status, odds, mode="safe", target=3.0):
    c = Coupon(mode=mode, source="auto", target_odds=target, total_odds=odds, prob=0.3, status=status,
               settled_odds=odds if status == "won" else 0.0, play_date=date(2026, 11, 16))
    session.add(c)
    session.commit()


def test_mode_stats_unit_roi(session):
    _coupon(session, "won", 3.0)
    _coupon(session, "lost", 3.2)
    _coupon(session, "lost", 2.9)
    _coupon(session, "open", 3.1)
    _coupon(session, "won", 2.0, mode="value", target=2.0)
    st = mode_stats(session, "safe")
    assert st["coupons"] == 3 and st["won"] == 1 and st["open"] == 1
    assert st["unit_roi"] == pytest.approx(0.0)
    assert st["by_target"][3.0]["coupons"] == 3
    assert mode_stats(session, "value")["unit_roi"] == pytest.approx(1.0)


def test_user_coupons_excluded_from_mode_stats(session):
    session.add(Coupon(mode="safe", source="user", target_odds=3, total_odds=3, prob=0.3, status="won",
                       settled_odds=3, play_date=date(2026, 11, 16)))
    session.commit()
    assert mode_stats(session, "safe")["coupons"] == 0


def test_singles_stats_by_market(session):
    ids = seed_synthetic_league(session, upcoming=3)
    for mid, market, res, odds in [(ids[0], "MS1", "won", 2.0), (ids[1], "MS1", "lost", 2.0), (ids[2], "UST25", "won", 1.8)]:
        session.add(SinglePick(mode="value", match_id=mid, market=market, odds=odds, prob=0.5,
                               play_date=date(2026, 11, 16), result=res))
    session.commit()
    total = singles_stats(session, "value")[0]
    assert total["n"] == 3 and total["won"] == 2 and total["unit_roi"] == pytest.approx((2.0 + 1.8 - 3) / 3)
    by = {r["key"]: r for r in singles_stats(session, "value", by="market")}
    assert by["MS 1"]["n"] == 2 and by["Toplam gol 2.5 Üst"]["won"] == 1


def test_live_calibration(session):
    ids = seed_synthetic_league(session, upcoming=2)
    for mid, p, hg in [(ids[0], 0.72, 2), (ids[1], 0.75, 0)]:
        session.add(Prediction(match_id=mid, kind="live", created_at=NOW, probs={"MS1": p, "MSX": 0.15, "MS2": 0.13}))
        m = session.get(Match, mid)
        m.status, m.home_goals, m.away_goals = "finished", hg, 1
    session.commit()
    bins = live_calibration(session, "1X2")
    assert bins == [[pytest.approx(0.735), 0.5, 2]]
