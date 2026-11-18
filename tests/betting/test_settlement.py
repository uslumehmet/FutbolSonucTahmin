from datetime import date, timedelta

import pytest

from app.betting.coupon import CouponDraft, save_coupon
from app.betting.selection import Candidate
from app.betting.settlement import coupon_outcome, settle_all, wallet_stats
from app.betting.wallets import auto_stake, create_wallet, place_bet
from app.models import Coupon, CouponPick, Match, SinglePick
from tests.helpers import NOW, seed_synthetic_league


def _setup(session, picks):
    """picks: list of (market, odds). Her seçim ayrı gelecek maçta."""
    ids = seed_synthetic_league(session, upcoming=len(picks))
    cands = [Candidate(mid, mk, od, 0.5) for mid, (mk, od) in zip(ids, picks)]
    total = 1.0
    for c in cands:
        total *= c.odds
    coupon = save_coupon(session, CouponDraft(cands, total, 0.5 ** len(cands)), "safe", 2.0, "auto", date(2026, 11, 16))
    w = create_wallet(session, "Kasa", "safe", 100)
    place_bet(session, w, coupon, 1_000)
    return ids, coupon, w


def _finish(session, mid, hg, ag):
    m = session.get(Match, mid)
    m.status, m.home_goals, m.away_goals = "finished", hg, ag
    session.commit()


def test_coupon_outcome_rules():
    assert coupon_outcome([("won", 1.5), ("lost", 2.0)]) == ("lost", 0.0)
    assert coupon_outcome([("won", 1.5), ("pending", 2.0)]) == ("open", 0.0)
    assert coupon_outcome([("void", 1.5), ("void", 2.0)]) == ("void", 1.0)
    assert coupon_outcome([("won", 1.5), ("void", 1.8)]) == ("won", 1.5)


def test_won_coupon_pays_once(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5), ("UST25", 2.0)])
    _finish(session, ids[0], 2, 0)
    _finish(session, ids[1], 2, 1)
    later = NOW + timedelta(days=2)
    settle_all(session, later)
    settle_all(session, later)
    session.refresh(w)
    assert coupon.status == "won" and coupon.settled_odds == pytest.approx(3.0)
    assert w.balance == 9_000 + 3_000


def test_any_lost_loses(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5), ("UST25", 2.0)])
    _finish(session, ids[0], 2, 0)
    _finish(session, ids[1], 0, 0)
    settle_all(session, NOW + timedelta(days=2))
    assert coupon.status == "lost" and w.balance == 9_000


def test_postponed_then_played_stays_void(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5), ("UST25", 2.0)])
    _finish(session, ids[1], 3, 1)
    m0 = session.get(Match, ids[0])
    settle_all(session, m0.kickoff_utc + timedelta(hours=49))
    assert session.get(Match, ids[0]).status == "postponed"
    pick0 = session.query(CouponPick).filter_by(coupon_id=coupon.id, match_id=ids[0]).one()
    assert pick0.result == "void"
    assert coupon.status == "won" and coupon.settled_odds == pytest.approx(2.0)
    _finish(session, ids[0], 0, 5)
    settle_all(session, m0.kickoff_utc + timedelta(days=5))
    session.refresh(pick0)
    assert pick0.result == "void" and coupon.status == "won"
    assert w.balance == 9_000 + 2_000


def test_all_void_refunds(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5)])
    session.get(Match, ids[0]).status = "cancelled"
    session.commit()
    settle_all(session, NOW + timedelta(days=1))
    assert coupon.status == "void" and w.balance == 10_000


def test_single_picks_settled(session):
    ids = seed_synthetic_league(session, upcoming=1)
    session.add(SinglePick(mode="value", match_id=ids[0], market="KGVAR", odds=1.8, prob=0.55, play_date=date(2026, 11, 16)))
    session.commit()
    _finish(session, ids[0], 1, 1)
    rep = settle_all(session, NOW + timedelta(days=2))
    assert session.query(SinglePick).one().result == "won" and rep.singles == 1


def test_wallet_busted(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5)])
    w.balance = 0
    session.commit()
    _finish(session, ids[0], 0, 1)
    settle_all(session, NOW + timedelta(days=2))
    session.refresh(w)
    assert w.busted is True and auto_stake(w) == 0


def test_open_bets_prevent_busted(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5)])
    w.balance = 0
    session.commit()
    settle_all(session, NOW)
    session.refresh(w)
    assert w.busted is False


def test_wallet_stats_roi(session):
    ids, coupon, w = _setup(session, [("MS1", 1.5)])
    _finish(session, ids[0], 2, 0)
    settle_all(session, NOW + timedelta(days=2))
    st = wallet_stats(session, w)
    assert (st.played, st.won, st.lost, st.open) == (1, 1, 0, 0)
    assert st.staked == 1_000 and st.returned == 1_500 and st.net == 500
    assert st.roi == pytest.approx(0.5)
    assert st.history[0][1] == 10_000 and st.history[-1][1] == 10_500


def test_wallet_equity_ignores_open_stakes_and_moves_on_settlement(session):
    """Kasa değeri = bakiye + açık kuponlardaki para: kupon oynanınca düşmez, sonuçlanınca değişir."""
    ids, coupon, w = _setup(session, [("MS1", 1.5)])
    st = wallet_stats(session, w)
    assert [v for _, v in st.equity] == [10_000]  # açık kupon: değer aynı
    assert st.max_drawdown == 0
    _finish(session, ids[0], 2, 0)
    settle_all(session, NOW + timedelta(days=2))
    st = wallet_stats(session, w)
    assert [v for _, v in st.equity] == [10_000, 10_500]
