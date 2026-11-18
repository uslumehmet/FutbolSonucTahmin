from datetime import datetime, timedelta

import pytest

from app.betting.autoplay import autoplay_window, run_autoplay
from app.betting.wallets import create_wallet, ensure_default_wallets
from app.config import TZ, save_settings
from app.model.predict import predict_upcoming
from app.model.store import ModelStore
from app.models import Bet, Coupon, CouponPick, Match, OddsQuote, SinglePick, Wallet
from tests.helpers import seed_synthetic_league

NOW = datetime(2026, 11, 16, 11, 0, tzinfo=TZ)


def _ready(session, upcoming=12):
    ids = seed_synthetic_league(session, upcoming=upcoming, now=NOW)
    for j, mid in enumerate(ids):  # hepsi otomatik oyun penceresinde başlasın
        session.get(Match, mid).kickoff_utc = NOW + timedelta(hours=2 + j)
    session.commit()
    # Değerli modun da kupon kurabilmesi için oranları modelden biraz yüksek yap
    preds = {p.match_id: p for p in predict_upcoming(session, ModelStore(session), NOW)}
    session.query(OddsQuote).delete()
    for mid in ids:
        for market, prob in preds[mid].probs.items():
            session.add(OddsQuote(match_id=mid, bookmaker="B365", market=market, odds=round(1.15 / prob, 2),
                                  fetched_at=NOW, source="api"))
    session.commit()
    ensure_default_wallets(session)
    return ids


def test_window():
    s, e = autoplay_window(NOW)
    assert s == datetime(2026, 11, 16, 12, 0, tzinfo=TZ) and e == datetime(2026, 11, 17, 11, 0, tzinfo=TZ)
    assert autoplay_window(datetime(2026, 11, 16, 15, 0, tzinfo=TZ))[1] == datetime(2026, 11, 17, 11, 0, tzinfo=TZ)
    assert autoplay_window(datetime(2026, 11, 16, 9, 30, tzinfo=TZ))[1] == datetime(2026, 11, 16, 11, 0, tzinfo=TZ)


def test_creates_coupons_for_both_modes_and_targets(session):
    _ready(session)
    rep = run_autoplay(session, NOW)
    coupons = session.query(Coupon).filter_by(source="auto").all()
    assert {(c.mode, c.target_odds) for c in coupons} == {(m, t) for m in ("safe", "value") for t in (2.0, 3.0, 5.0)}
    for c in coupons:
        assert c.target_odds <= c.total_odds <= c.target_odds * 1.3
    assert rep.coupons == 6 and rep.bets == 12
    assert session.query(SinglePick).filter_by(mode="safe").count() > 0
    assert session.query(SinglePick).filter_by(mode="value").count() > 0


def test_idempotent(session):
    _ready(session)
    run_autoplay(session, NOW)
    balances = [w.balance for w in session.query(Wallet).order_by(Wallet.id)]
    rep2 = run_autoplay(session, NOW + timedelta(hours=2))
    assert rep2.coupons == 0
    assert session.query(Coupon).count() == 6
    assert [w.balance for w in session.query(Wallet).order_by(Wallet.id)] == balances


def test_same_mode_wallets_share_coupon_different_stakes(session):
    _ready(session)
    run_autoplay(session, NOW)
    small = session.query(Wallet).filter_by(name="Sistem · Güvenli 100 ₺").one()
    big = session.query(Wallet).filter_by(name="Sistem · Güvenli 1.000 ₺").one()
    sb = {b.coupon_id: b.stake for b in session.query(Bet).filter_by(wallet_id=small.id)}
    bb = {b.coupon_id: b.stake for b in session.query(Bet).filter_by(wallet_id=big.id)}
    assert set(sb) == set(bb)
    # her kupon güncel bakiyenin %3ü: 1.000 → 30, 970 → 29, 941 → 28
    assert sorted(sb.values()) == [300, 300, 300] and sorted(bb.values()) == [2_800, 2_900, 3_000]
    user = session.query(Wallet).filter_by(owner="user").one()
    assert session.query(Bet).filter_by(wallet_id=user.id).count() == 0


def test_custom_wallet_also_plays(session):
    _ready(session)
    w = create_wallet(session, "Deneme 500", "value", 500)
    run_autoplay(session, NOW)
    assert session.query(Bet).filter_by(wallet_id=w.id).count() == 3


def test_odds_change_after_autoplay_keeps_picks(session):
    _ready(session)
    run_autoplay(session, NOW)
    before = {(p.id, p.odds) for p in session.query(CouponPick)}
    session.query(OddsQuote).update({"odds": 9.99})
    session.commit()
    assert {(p.id, p.odds) for p in session.query(CouponPick)} == before


def test_busted_wallet_skipped(session):
    _ready(session)
    w = session.query(Wallet).filter_by(name="Sistem · Değerli 100 ₺").one()
    w.busted = True
    session.commit()
    run_autoplay(session, NOW)
    assert session.query(Bet).filter_by(wallet_id=w.id).count() == 0


def test_match_starting_within_hour_excluded(session):
    ids = _ready(session)
    soon = session.get(Match, ids[0])
    soon.kickoff_utc = NOW + timedelta(minutes=30)
    session.commit()
    run_autoplay(session, NOW)
    assert session.query(CouponPick).filter_by(match_id=ids[0]).count() == 0
    assert session.query(SinglePick).filter_by(match_id=ids[0]).count() == 0


def test_skips_when_no_candidates(session):
    ensure_default_wallets(session)
    rep = run_autoplay(session, NOW)
    assert rep.coupons == 0 and len(rep.skipped) == 6


def test_custom_targets_from_settings(session):
    _ready(session)
    save_settings(session, autoplay_targets=(2.0,))
    run_autoplay(session, NOW)
    assert {c.target_odds for c in session.query(Coupon)} == {2.0}


def test_autoplay_creates_default_wallets_if_missing(session):
    ids = seed_synthetic_league(session, upcoming=6, now=NOW)
    for j, mid in enumerate(ids):
        session.get(Match, mid).kickoff_utc = NOW + timedelta(hours=2 + j)
    session.commit()
    predict_upcoming(session, ModelStore(session), NOW)
    assert session.query(Wallet).count() == 0
    rep = run_autoplay(session, NOW)
    assert session.query(Wallet).count() == 5
    assert rep.coupons == 0 or rep.bets > 0
