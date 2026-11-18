"""Sonuçlandırma: seçimler, kuponlar, bahisler ve kasalar (idempotent)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.model.markets import pick_wins
from app.models import Bet, Coupon, CouponPick, Match, SinglePick, Wallet

POSTPONE_AFTER = timedelta(hours=48)
VOID_STATUSES = {"postponed", "cancelled", "abandoned"}


@dataclass
class SettleReport:
    picks: int = 0
    coupons: int = 0
    bets: int = 0
    singles: int = 0
    busted: int = 0


def mark_postponements(session, now: datetime) -> int:
    rows = session.query(Match).filter(Match.status == "scheduled", Match.kickoff_utc < now - POSTPONE_AFTER).all()
    for m in rows:
        m.status = "postponed"
    session.commit()
    return len(rows)


def coupon_outcome(results: list[tuple[str, float]]) -> tuple[str, float]:
    states = [r for r, _ in results]
    if "lost" in states:
        return "lost", 0.0
    if "pending" in states:
        return "open", 0.0
    if all(r == "void" for r in states):
        return "void", 1.0
    return "won", math.prod(o for r, o in results if r == "won")


def _pick_result(match: Match) -> str | None:
    if match.status == "finished" and match.home_goals is not None and match.away_goals is not None:
        return "finished"
    if match.status in VOID_STATUSES:
        return "void"
    return None


def _settle_pick(pick, match: Match) -> bool:
    state = _pick_result(match)
    if state is None:
        return False
    if state == "void":
        pick.result = "void"
    else:
        pick.result = "won" if pick_wins(pick.market, match.home_goals, match.away_goals) else "lost"
    return True


def settle_all(session, now: datetime) -> SettleReport:
    rep = SettleReport()
    mark_postponements(session, now)
    for pick in session.query(CouponPick).filter_by(result="pending").all():
        if _settle_pick(pick, session.get(Match, pick.match_id)):
            rep.picks += 1
    for sp in session.query(SinglePick).filter_by(result="pending").all():
        if _settle_pick(sp, session.get(Match, sp.match_id)):
            rep.singles += 1
    session.flush()
    for coupon in session.query(Coupon).filter_by(status="open").all():
        status, odds = coupon_outcome([(p.result, p.odds) for p in coupon.picks])
        if status != "open":
            coupon.status, coupon.settled_odds = status, odds
            rep.coupons += 1
    session.flush()
    open_bets = (session.query(Bet).join(Coupon, Bet.coupon_id == Coupon.id)
                 .filter(Bet.payout.is_(None), Coupon.status.in_(("won", "lost", "void"))).all())
    for bet in open_bets:
        coupon = session.get(Coupon, bet.coupon_id)
        if coupon.status == "won":
            bet.payout = int(math.floor(bet.stake * coupon.settled_odds + 0.5))
        elif coupon.status == "void":
            bet.payout = bet.stake
        else:
            bet.payout = 0
        bet.settled_at = now
        wallet = session.get(Wallet, bet.wallet_id)
        wallet.balance += bet.payout
        rep.bets += 1
    session.flush()
    for wallet in session.query(Wallet).filter_by(busted=False).all():
        if wallet.balance < 100:
            pending = session.query(Bet).filter_by(wallet_id=wallet.id, payout=None).count()
            if not pending:
                wallet.busted = True
                rep.busted += 1
    session.commit()
    return rep


@dataclass
class WalletStats:
    played: int = 0
    won: int = 0
    lost: int = 0
    void: int = 0
    open: int = 0
    staked: int = 0
    returned: int = 0
    net: int = 0
    roi: float = 0.0
    max_drawdown: int = 0
    history: list[tuple[datetime, int]] = field(default_factory=list)
    # kasa değeri (bakiye + açık kuponlardaki para): yalnızca kupon sonuçlanınca değişir
    equity: list[tuple[datetime, int]] = field(default_factory=list)


def wallet_stats(session, wallet: Wallet) -> WalletStats:
    st = WalletStats()
    events: list[tuple[datetime, int]] = []
    results: list[tuple[datetime, int]] = []
    for bet in session.query(Bet).filter_by(wallet_id=wallet.id).all():
        coupon = session.get(Coupon, bet.coupon_id)
        events.append((bet.placed_at, -bet.stake))
        if bet.payout is None:
            st.open += 1
            continue
        st.played += 1
        st.staked += bet.stake
        st.returned += bet.payout
        events.append((bet.settled_at or bet.placed_at, bet.payout))
        results.append((bet.settled_at or bet.placed_at, bet.payout - bet.stake))
        if coupon.status == "won":
            st.won += 1
        elif coupon.status == "lost":
            st.lost += 1
        else:
            st.void += 1
    st.net = st.returned - st.staked
    st.roi = st.net / st.staked if st.staked else 0.0
    balance = wallet.start_balance
    st.history = [(wallet.created_at, balance)]
    for ts, delta in sorted(events, key=lambda e: e[0]):
        balance += delta
        st.history.append((ts, balance))
    value = wallet.start_balance
    st.equity = [(wallet.created_at, value)]
    peak = value
    for ts, delta in sorted(results, key=lambda e: e[0]):
        value += delta
        if st.equity and st.equity[-1][0] == ts:
            st.equity[-1] = (ts, value)  # aynı anda sonuçlanan kuponlar tek nokta
        else:
            st.equity.append((ts, value))
        peak = max(peak, value)
        st.max_drawdown = max(st.max_drawdown, peak - value)  # açık kupon parası düşüş sayılmaz
    return st
