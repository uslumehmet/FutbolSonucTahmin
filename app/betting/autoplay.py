"""Sistemin günlük otomatik oyunu: iki mod × hedef oranlar + tekli kayıtlar."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from app.betting.coupon import best_coupon, save_coupon
from app.betting.selection import eligible, match_candidates, safe_pick, value_pick
from app.betting.wallets import auto_stake, ensure_default_wallets, place_bet
from app.config import TZ, get_settings
from app.models import JobRun, SinglePick, Wallet

AUTOPLAY_HOUR = 11
MODES = ("safe", "value")
MODE_LABELS = {"safe": "Güvenli", "value": "Değerli"}


@dataclass
class AutoplayReport:
    coupons: int = 0
    bets: int = 0
    singles: int = 0
    skipped: list[str] = field(default_factory=list)


def autoplay_window(now: datetime) -> tuple[datetime, datetime]:
    start = now + timedelta(hours=1)
    local = start.astimezone(TZ)
    end = datetime.combine(local.date(), time(AUTOPLAY_HOUR, 0), tzinfo=TZ)
    if end <= local:
        end += timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def run_autoplay(session, now: datetime) -> AutoplayReport:
    rep = AutoplayReport()
    run_key = now.astimezone(TZ).date().isoformat()
    if session.query(JobRun).filter_by(job="autoplay", run_key=run_key).first():
        return rep
    ensure_default_wallets(session)
    s = get_settings(session)
    start, end = autoplay_window(now)
    cands = match_candidates(session, start, end, s)
    play_date = now.astimezone(TZ).date()
    for mode in MODES:
        pool = {mid: eligible(cs, mode, s) for mid, cs in cands.items()}
        pool = {mid: cs for mid, cs in pool.items() if cs}
        wallets = session.query(Wallet).filter_by(owner=mode, busted=False).order_by(Wallet.id).all()
        for target in s.autoplay_targets:
            draft = best_coupon(pool, target, max_picks=s.coupon_max_picks, upper_factor=s.coupon_upper_factor)
            if draft is None:
                rep.skipped.append(f"{MODE_LABELS[mode]} {target:g}: uygun kupon yok")
                continue
            coupon = save_coupon(session, draft, mode, target, "auto", play_date)
            rep.coupons += 1
            for w in wallets:
                stake = auto_stake(w)
                if stake > 0:
                    place_bet(session, w, coupon, stake)
                    rep.bets += 1
        for mid, cs in pool.items():
            pick = safe_pick(cs, s.safe_min_odds) if mode == "safe" else value_pick(cs, s.value_min_prob, s.value_min_edge)
            if pick is not None:
                session.add(SinglePick(mode=mode, match_id=mid, market=pick.market, odds=pick.odds, prob=pick.prob,
                                       play_date=play_date))
                rep.singles += 1
    session.add(JobRun(job="autoplay", run_key=run_key, finished_at=now))
    session.commit()
    return rep
