from datetime import date

import pytest

from app.betting.wallets import (
    WalletError, auto_stake, create_wallet, delete_wallet, ensure_default_wallets, place_bet, reset_wallet,
)
from app.models import Bet, Coupon, Wallet


def _coupon(session):
    c = Coupon(mode="safe", source="auto", target_odds=2.0, total_odds=2.1, prob=0.5, status="open",
               play_date=date(2026, 11, 16))
    session.add(c)
    session.commit()
    return c


def test_default_wallets_created_once(session):
    ensure_default_wallets(session)
    ensure_default_wallets(session)
    ws = session.query(Wallet).order_by(Wallet.id).all()
    assert [(w.owner, w.start_balance) for w in ws] == [
        ("safe", 10_000), ("safe", 100_000), ("value", 10_000), ("value", 100_000), ("user", 100_000)]
    assert ws[0].stake_rule == "percent" and ws[0].stake_value == 3.0


@pytest.mark.parametrize("balance,stake", [(10_000, 300), (100_000, 3_000), (140, 100), (90, 0), (5_050, 200)])
def test_auto_stake_percent(session, balance, stake):
    w = create_wallet(session, "x", "safe", 100)
    w.balance = balance
    assert auto_stake(w) == stake


def test_auto_stake_fixed_capped_by_balance(session):
    w = create_wallet(session, "x", "value", 100, stake_rule="fixed", stake_value=10)
    assert auto_stake(w) == 1_000
    w.balance = 750
    assert auto_stake(w) == 700


def test_place_bet_insufficient(session):
    w = create_wallet(session, "Benim", "user", 1000)
    with pytest.raises(WalletError, match="Bakiye yetersiz"):
        place_bet(session, w, _coupon(session), 150_000)
    assert w.balance == 100_000


def test_stake_must_be_whole_tl(session):
    w = create_wallet(session, "Benim", "user", 1000)
    with pytest.raises(WalletError, match="tam"):
        place_bet(session, w, _coupon(session), 1_050)
    with pytest.raises(WalletError, match="En az 1"):
        place_bet(session, w, _coupon(session), 0)


def test_place_bet_deducts(session):
    w = create_wallet(session, "Benim", "user", 1000)
    bet = place_bet(session, w, _coupon(session), 1_000)
    assert w.balance == 99_000 and bet.payout is None


def test_busted_wallet_cannot_bet(session):
    w = create_wallet(session, "x", "safe", 100)
    w.busted = True
    with pytest.raises(WalletError, match="battı"):
        place_bet(session, w, _coupon(session), 100)


def test_create_wallet_validates_start(session):
    with pytest.raises(WalletError):
        create_wallet(session, "x", "user", 0)


def test_reset_and_delete(session):
    w = create_wallet(session, "Benim", "user", 1000)
    place_bet(session, w, _coupon(session), 5_000)
    reset_wallet(session, w)
    assert w.balance == 100_000 and session.query(Bet).filter_by(wallet_id=w.id).count() == 0
    delete_wallet(session, w)
    assert session.query(Wallet).count() == 0
