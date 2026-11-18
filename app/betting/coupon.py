"""Kupon optimizasyonu: hedef oran aralığında tutma ihtimalini en yükseğe çıkarma."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from app.betting.selection import Candidate
from app.models import Coupon, CouponPick

BUCKET = 0.01        # log-oran kova genişliği
TOP_PER_MATCH = 5    # maç başına değerlendirilen en olası aday sayısı


@dataclass
class CouponDraft:
    picks: list[Candidate]
    total_odds: float
    prob: float

    @property
    def expected_return(self) -> float:
        return self.prob * self.total_odds - 1


def best_coupon(cands_by_match: dict[int, list[Candidate]], target: float, max_picks: int = 6,
                upper_factor: float = 1.3, exclude: frozenset[int] | set[int] = frozenset()) -> CouponDraft | None:
    lo, hi = math.log(target), math.log(target * upper_factor)
    # durum: (seçim sayısı, kova) -> (∑log p, ∑log oran, seçimler)
    states: dict[tuple[int, int], tuple[float, float, tuple[Candidate, ...]]] = {(0, 0): (0.0, 0.0, ())}
    for match_id in sorted(cands_by_match):
        if match_id in exclude:
            continue
        top = sorted(cands_by_match[match_id], key=lambda c: c.prob, reverse=True)[:TOP_PER_MATCH]
        top = [c for c in top if c.odds > 1.0 and c.prob > 0]
        if not top:
            continue
        new = dict(states)
        for (n, _), (lp, lsum, picks) in states.items():
            if n >= max_picks:
                continue
            for c in top:
                s = lsum + math.log(c.odds)
                if s > hi + 1e-12:
                    continue
                key = (n + 1, int(s / BUCKET))
                cand = (lp + math.log(c.prob), s, picks + (c,))
                cur = new.get(key)
                if cur is None or cand[0] > cur[0]:
                    new[key] = cand
        states = new
    best = None
    for (n, _), (lp, s, picks) in states.items():
        if n >= 1 and lo - 1e-12 <= s <= hi + 1e-12 and (best is None or lp > best[0]):
            best = (lp, s, picks)
    if best is None:
        return None
    picks = sorted(best[2], key=lambda c: c.match_id)
    return CouponDraft(picks=list(picks), total_odds=math.prod(c.odds for c in picks),
                       prob=math.prod(c.prob for c in picks))


def build_coupons(cands_by_match: dict[int, list[Candidate]], target: float, max_picks: int = 6,
                  upper_factor: float = 1.3, n_alternatives: int = 2) -> list[CouponDraft]:
    out: list[CouponDraft] = []
    used: set[int] = set()
    for _ in range(1 + n_alternatives):
        c = best_coupon(cands_by_match, target, max_picks, upper_factor, exclude=frozenset(used))
        if c is None:
            break
        out.append(c)
        used |= {p.match_id for p in c.picks}
    return out


def save_coupon(session, draft: CouponDraft, mode: str, target: float, source: str, play_date: date,
                status: str = "open") -> Coupon:
    coupon = Coupon(mode=mode, source=source, target_odds=target, total_odds=round(draft.total_odds, 4),
                    prob=draft.prob, status=status, play_date=play_date)
    coupon.picks = [CouponPick(match_id=p.match_id, market=p.market, odds=p.odds, prob=p.prob) for p in draft.picks]
    session.add(coupon)
    session.commit()
    return coupon
