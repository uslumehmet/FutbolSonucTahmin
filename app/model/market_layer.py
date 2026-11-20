"""Katman 3: modelin tahminini bahisçi oranıyla birleştirme.

Ölçüm (20 Kasım 2026, 2024-10 – 2026-11 test dönemi, DraftKings açılış oranı = maçtan ~1 gün önceki oran):
- Maç sonucu: piyasa logloss 0,976, model 0,993 (fark anlamlı). En iyisi modele küçük ağırlık (0,1) ve piyasaya
  hafif keskinleştirme; modelin ağırlığı 0,2'yi geçince sonuç kötüleşiyor.
- 2.5 Alt/Üst: model ile piyasa yaklaşık eşit; en iyisi model ağırlığı 0,25.
- Diğer bahisler (1.5/3.5, karşılıklı gol, takım golleri) için oran geçmişi az; bunlar, gol beklentisi maç sonucu ve
  2.5 hedeflerine uydurulmuş skor tablosundan hesaplanır. Ölçümde hepsi modelden daha iyi çıktı (karşılıklı gol
  logloss 0,695 → 0,684, anlamlı).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize

from app.model.markets import market_probs, probs_from_1x2, score_matrix

W_MODEL_1X2 = 0.10   # log-lineer havuzda modelin üssü
W_MARKET_1X2 = 0.99  # piyasanın üssü (0,9 × 1,1: oranlar hafifçe keskinleştirilir)
W_MODEL_OU25 = 0.25  # 2.5 Alt/Üst'te logit ortalamasında modelin ağırlığı
EPS = 1e-6


def demargin(odds: dict[str, float], keys: list[str]) -> list[float] | None:
    """Bahisçi payını orantılı olarak çıkarır; bir oran eksikse ya da geçersizse None."""
    vals = [odds.get(k) for k in keys]
    if any(v is None or v <= 1.0 for v in vals):
        return None
    inv = [1.0 / v for v in vals]
    total = sum(inv)
    return [x / total for x in inv]


def blend_1x2(model: list[float], market: list[float]) -> list[float]:
    z = [W_MODEL_1X2 * math.log(max(p, EPS)) + W_MARKET_1X2 * math.log(max(q, EPS)) for p, q in zip(model, market)]
    top = max(z)
    e = [math.exp(v - top) for v in z]
    s = sum(e)
    return [v / s for v in e]


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def blend_binary(model: float, market: float, w_model: float = W_MODEL_OU25) -> float:
    z = w_model * _logit(model) + (1 - w_model) * _logit(market)
    return 1 / (1 + math.exp(-z))


def fit_goals(p_home: float, p_away: float, p_over25: float, rho: float, lam0: float, mu0: float
              ) -> tuple[float, float]:
    """Ev ve deplasman gol beklentisini, skor tablosu bu üç ihtimale en yakın olacak şekilde bulur."""
    def loss(v):
        p = market_probs(score_matrix(math.exp(v[0]), math.exp(v[1]), rho, 8))
        return (p["MS1"] - p_home) ** 2 + (p["MS2"] - p_away) ** 2 + (p["UST25"] - p_over25) ** 2

    start = [math.log(max(lam0, 0.2)), math.log(max(mu0, 0.2))]
    res = minimize(loss, start, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-10, "maxiter": 400})
    lam, mu = (float(np.clip(math.exp(x), 0.05, 6.0)) for x in res.x)
    return lam, mu


def apply_market(probs: dict[str, float], lam: float, mu: float, rho: float, odds: dict[str, float]
                 ) -> tuple[dict[str, float], float, float, np.ndarray] | None:
    """Model ihtimallerini oranlarla birleştirir. Maç sonucu oranı yoksa None (model olduğu gibi kalır).

    Döner: (yeni ihtimaller, gol beklentileri, skor tablosu). Maç sonucu ve 2.5 Alt/Üst doğrudan birleşik
    değerlerdir; çifte şans maç sonucundan, diğer bahisler uydurulmuş skor tablosundan gelir.
    """
    q1x2 = demargin(odds, ["MS1", "MSX", "MS2"])
    if q1x2 is None or not all(k in probs for k in ("MS1", "MSX", "MS2", "UST25")):
        return None
    p1, px, p2 = blend_1x2([probs["MS1"], probs["MSX"], probs["MS2"]], q1x2)
    qou = demargin(odds, ["UST25", "ALT25"])
    over = blend_binary(probs["UST25"], qou[0]) if qou else probs["UST25"]
    lam2, mu2 = fit_goals(p1, p2, over, rho, lam, mu)
    matrix = score_matrix(lam2, mu2, rho)
    out = probs_from_1x2(p1, px, p2, market_probs(matrix))
    out["UST25"], out["ALT25"] = over, 1 - over
    return out, lam2, mu2, matrix
