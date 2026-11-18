import itertools
import math
import time
from datetime import date

import numpy as np
import pytest

from app.betting.coupon import best_coupon, build_coupons, save_coupon
from app.betting.selection import Candidate
from app.models import CouponPick
from tests.helpers import seed_synthetic_league

MARKETS = ["MS1", "MSX", "MS2", "UST25", "ALT25", "KGVAR", "KGYOK", "CS1X", "CS12", "CSX2"]


def random_cands(seed, n_matches, per_match=3):
    rng = np.random.default_rng(seed)
    out = {}
    for mid in range(1, n_matches + 1):
        cs = []
        for market in rng.choice(MARKETS, per_match, replace=False):
            odds = float(np.round(rng.uniform(1.15, 3.5), 2))
            prob = float(min(0.95, max(0.05, 1 / odds + rng.normal(0, 0.05))))
            cs.append(Candidate(mid, str(market), odds, prob))
        out[mid] = cs
    return out


@pytest.mark.parametrize("target", [2.0, 3.0, 5.0, 10.0])
def test_total_odds_within_range(target):
    c = best_coupon(random_cands(1, 20), target)
    assert c is not None
    assert target <= c.total_odds <= target * 1.3
    assert c.total_odds == pytest.approx(math.prod(p.odds for p in c.picks))
    assert c.prob == pytest.approx(math.prod(p.prob for p in c.picks))


def test_one_pick_per_match():
    c = best_coupon(random_cands(2, 15), 5.0)
    ids = [p.match_id for p in c.picks]
    assert len(ids) == len(set(ids)) and len(ids) <= 6


def _brute(cands, target, max_picks=6, upper=1.3):
    best = None
    matches = list(cands)
    for k in range(1, max_picks + 1):
        for combo in itertools.combinations(matches, k):
            for picks in itertools.product(*(cands[m] for m in combo)):
                odds = math.prod(p.odds for p in picks)
                if target <= odds <= target * upper:
                    prob = math.prod(p.prob for p in picks)
                    if best is None or prob > best:
                        best = prob
    return best


@pytest.mark.parametrize("seed,target", [(3, 3.0), (4, 5.0), (5, 2.0)])
def test_matches_bruteforce(seed, target):
    cands = random_cands(seed, 8)
    dp = best_coupon(cands, target)
    assert dp.prob >= 0.995 * _brute(cands, target)


def test_none_when_unreachable():
    cands = {i: [Candidate(i, "MS1", 1.20, 0.8)] for i in range(1, 6)}
    assert best_coupon(cands, 5.0, max_picks=2) is None


def test_alternatives_use_different_matches():
    coupons = build_coupons(random_cands(6, 30), 3.0, n_alternatives=2)
    assert len(coupons) == 3
    used = [set(p.match_id for p in c.picks) for c in coupons]
    assert not (used[0] & used[1]) and not (used[0] & used[2]) and not (used[1] & used[2])


def test_fast_enough_for_60_matches():
    cands = random_cands(7, 60, per_match=8)
    t = time.time()
    assert best_coupon(cands, 10.0) is not None
    assert time.time() - t < 3.0


def test_save_coupon_snapshots(session):
    ids = seed_synthetic_league(session, upcoming=2)
    draft = best_coupon({ids[0]: [Candidate(ids[0], "MS1", 1.8, 0.6)], ids[1]: [Candidate(ids[1], "UST25", 1.7, 0.6)]}, 3.0)
    c = save_coupon(session, draft, "safe", 3.0, "auto", date(2026, 11, 16))
    picks = session.query(CouponPick).filter_by(coupon_id=c.id).all()
    assert {(p.market, p.odds, p.prob) for p in picks} == {("MS1", 1.8, 0.6), ("UST25", 1.7, 0.6)}
    assert c.status == "open" and c.total_odds == pytest.approx(3.06) and c.prob == pytest.approx(0.36)
