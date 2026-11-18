"""Sanal kasalar. Tüm tutarlar kuruş (tam sayı)."""
from __future__ import annotations

import math

from app.models import Bet, Coupon, Wallet, utcnow

DEFAULT_WALLETS = [
    ("Sistem · Güvenli 100 ₺", "safe", 100),
    ("Sistem · Güvenli 1.000 ₺", "safe", 1000),
    ("Sistem · Değerli 100 ₺", "value", 100),
    ("Sistem · Değerli 1.000 ₺", "value", 1000),
    ("Benim Kasam", "user", 1000),
]
OWNER_LABELS = {"user": "Ben", "safe": "Sistem · Güvenli", "value": "Sistem · Değerli"}


class WalletError(Exception):
    pass


def create_wallet(session, name: str, owner: str, start_tl: int, stake_rule: str = "percent",
                  stake_value: float = 3.0) -> Wallet:
    if owner not in OWNER_LABELS:
        raise WalletError("Geçersiz oynayan")
    if int(start_tl) < 1:
        raise WalletError("Başlangıç parası en az 1 ₺ olmalı")
    if stake_rule not in ("percent", "fixed") or float(stake_value) <= 0:
        raise WalletError("Geçersiz tutar kuralı")
    w = Wallet(name=name.strip() or "Kasa", owner=owner, start_balance=int(start_tl) * 100,
               balance=int(start_tl) * 100, stake_rule=stake_rule, stake_value=float(stake_value), busted=False)
    session.add(w)
    session.commit()
    return w


def ensure_default_wallets(session) -> None:
    if session.query(Wallet).count():
        return
    for name, owner, start in DEFAULT_WALLETS:
        if owner == "user":
            create_wallet(session, name, owner, start, stake_rule="fixed", stake_value=10)
        else:
            create_wallet(session, name, owner, start, stake_rule="percent", stake_value=3.0)


def auto_stake(wallet: Wallet) -> int:
    if wallet.busted:
        return 0
    if wallet.stake_rule == "percent":
        stake = max(100, math.floor(wallet.balance * wallet.stake_value / 100 / 100 + 0.5) * 100)
    else:
        stake = int(round(wallet.stake_value)) * 100
    if stake > wallet.balance:
        stake = wallet.balance // 100 * 100
    return stake if stake >= 100 else 0


def place_bet(session, wallet: Wallet, coupon: Coupon, stake: int) -> Bet:
    if wallet.busted:
        raise WalletError("Bu kasa battı; yeni kupon oynanamaz")
    if stake < 100:
        raise WalletError("En az 1 ₺ yatırılabilir")
    if stake % 100:
        raise WalletError("Tutar tam ₺ olmalı")
    if stake > wallet.balance:
        raise WalletError("Bakiye yetersiz")
    wallet.balance -= stake
    bet = Bet(wallet_id=wallet.id, coupon_id=coupon.id, stake=stake, placed_at=utcnow())
    session.add(bet)
    session.commit()
    return bet


def reset_wallet(session, wallet: Wallet) -> None:
    session.query(Bet).filter_by(wallet_id=wallet.id).delete()
    wallet.balance = wallet.start_balance
    wallet.busted = False
    session.commit()


def delete_wallet(session, wallet: Wallet) -> None:
    session.query(Bet).filter_by(wallet_id=wallet.id).delete()
    session.delete(wallet)
    session.commit()
