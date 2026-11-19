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


def _key(draft) -> set[tuple[int, str]]:
    return {(p.match_id, p.market) for p in draft.picks}


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
        # Kasalar en düşük ihtimal sınırına göre gruplanır. Sınırsız kuponlar her zaman kurulur:
        # Performans'taki Güvenli–Değerli karşılaştırması onlarla yapılır.
        groups: dict[float, list[Wallet]] = {0.0: []}
        for w in wallets:
            groups.setdefault(w.min_prob or 0.0, []).append(w)
        for target in s.autoplay_targets:
            base = best_coupon(pool, target, max_picks=s.coupon_max_picks, upper_factor=s.coupon_upper_factor)
            base_coupon = None
            if base is None:
                rep.skipped.append(f"{MODE_LABELS[mode]} {target:g}: uygun kupon yok")
            else:
                base_coupon = save_coupon(session, base, mode, target, "auto", play_date)
                rep.coupons += 1
            for limit, group in sorted(groups.items()):
                coupon = base_coupon
                if limit > 0 and group:
                    strict = {mid: [c for c in cs if c.prob >= limit] for mid, cs in pool.items()}
                    strict = {mid: cs for mid, cs in strict.items() if cs}
                    draft = best_coupon(strict, target, max_picks=s.coupon_max_picks,
                                        upper_factor=s.coupon_upper_factor)
                    if draft is None:
                        rep.skipped.append(f"{MODE_LABELS[mode]} {target:g} (en az %{round(limit * 100)}): "
                                           "uygun kupon yok")
                        continue
                    if base is None or _key(draft) != _key(base):
                        coupon = save_coupon(session, draft, mode, target, "auto", play_date, min_prob=limit)
                        rep.coupons += 1
                if coupon is None:
                    continue
                for w in group:
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
