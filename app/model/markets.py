"""Skor matrisi ve bahis pazarlarının olasılıkları."""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson

MARKETS = ["MS1", "MSX", "MS2", "CS1X", "CS12", "CSX2",
           "UST15", "ALT15", "UST25", "ALT25", "UST35", "ALT35", "KGVAR", "KGYOK",
           "EVUST05", "EVALT05", "EVUST15", "EVALT15", "DEPUST05", "DEPALT05", "DEPUST15", "DEPALT15"]

MARKET_LABELS = {
    "MS1": "MS 1", "MSX": "MS X", "MS2": "MS 2",
    "CS1X": "ÇŞ 1X", "CS12": "ÇŞ 12", "CSX2": "ÇŞ X2",
    "UST15": "1.5 Üst", "ALT15": "1.5 Alt",
    "UST25": "2.5 Üst", "ALT25": "2.5 Alt",
    "UST35": "3.5 Üst", "ALT35": "3.5 Alt",
    "KGVAR": "KG Var", "KGYOK": "KG Yok",
    "EVUST05": "Ev sahibi 0.5 Üst", "EVALT05": "Ev sahibi 0.5 Alt",
    "EVUST15": "Ev sahibi 1.5 Üst", "EVALT15": "Ev sahibi 1.5 Alt",
    "DEPUST05": "Deplasman 0.5 Üst", "DEPALT05": "Deplasman 0.5 Alt",
    "DEPUST15": "Deplasman 1.5 Üst", "DEPALT15": "Deplasman 1.5 Alt",
}

MARKET_HELP = {
    "MS1": "Maç sonucu 1: maçı ev sahibi kazanır.",
    "MSX": "Maç sonucu X: maç berabere biter.",
    "MS2": "Maç sonucu 2: maçı deplasman takımı kazanır.",
    "CS1X": "Çifte şans 1X: ev sahibi kazanır ya da berabere biter (deplasman kazanmazsa tutar).",
    "CS12": "Çifte şans 12: bir takım kazanır, beraberlik olmaz.",
    "CSX2": "Çifte şans X2: deplasman kazanır ya da berabere biter (ev sahibi kazanmazsa tutar).",
    "UST15": "1.5 Üst: maçta toplam en az 2 gol olur.",
    "ALT15": "1.5 Alt: maçta toplam en fazla 1 gol olur.",
    "UST25": "2.5 Üst: maçta toplam en az 3 gol olur.",
    "ALT25": "2.5 Alt: maçta toplam en fazla 2 gol olur.",
    "UST35": "3.5 Üst: maçta toplam en az 4 gol olur.",
    "ALT35": "3.5 Alt: maçta toplam en fazla 3 gol olur.",
    "KGVAR": "Karşılıklı gol var: iki takım da en az birer gol atar.",
    "KGYOK": "Karşılıklı gol yok: takımlardan en az biri gol atamaz.",
    "EVUST05": "Ev sahibi 0.5 Üst: ev sahibi en az 1 gol atar.",
    "EVALT05": "Ev sahibi 0.5 Alt: ev sahibi gol atamaz.",
    "EVUST15": "Ev sahibi 1.5 Üst: ev sahibi en az 2 gol atar.",
    "EVALT15": "Ev sahibi 1.5 Alt: ev sahibi en fazla 1 gol atar.",
    "DEPUST05": "Deplasman 0.5 Üst: deplasman takımı en az 1 gol atar.",
    "DEPALT05": "Deplasman 0.5 Alt: deplasman takımı gol atamaz.",
    "DEPUST15": "Deplasman 1.5 Üst: deplasman takımı en az 2 gol atar.",
    "DEPALT15": "Deplasman 1.5 Alt: deplasman takımı en fazla 1 gol atar.",
}

MARKET_SECTIONS = [
    ("Maç sonucu", ["MS1", "MSX", "MS2"]),
    ("Çifte şans", ["CS1X", "CS12", "CSX2"]),
    ("Toplam gol", ["UST15", "ALT15", "UST25", "ALT25", "UST35", "ALT35"]),
    ("Karşılıklı gol", ["KGVAR", "KGYOK"]),
    ("Ev sahibi golleri", ["EVUST05", "EVALT05", "EVUST15", "EVALT15"]),
    ("Deplasman golleri", ["DEPUST05", "DEPALT05", "DEPUST15", "DEPALT15"]),
]

MARKET_GROUP = {
    "MS1": "1X2", "MSX": "1X2", "MS2": "1X2",
    "CS1X": "DC", "CS12": "DC", "CSX2": "DC",
    "UST15": "OU15", "ALT15": "OU15",
    "UST25": "OU25", "ALT25": "OU25",
    "UST35": "OU35", "ALT35": "OU35",
    "KGVAR": "BTTS", "KGYOK": "BTTS",
    "EVUST05": "H05", "EVALT05": "H05", "EVUST15": "H15", "EVALT15": "H15",
    "DEPUST05": "A05", "DEPALT05": "A05", "DEPUST15": "A15", "DEPALT15": "A15",
}


def score_matrix(lam_home: float, lam_away: float, rho: float = 0.0, max_goals: int = 10) -> np.ndarray:
    goals = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(goals, lam_home), poisson.pmf(goals, lam_away))
    if rho:
        m[0, 0] *= 1 - lam_home * lam_away * rho
        m[0, 1] *= 1 + lam_home * rho
        m[1, 0] *= 1 + lam_away * rho
        m[1, 1] *= 1 - rho
        m = np.clip(m, 0.0, None)
    return m / m.sum()


def market_probs(m: np.ndarray) -> dict[str, float]:
    n = m.shape[0]
    h, a = np.indices((n, n))
    p1 = float(m[h > a].sum())
    px = float(np.trace(m))
    p2 = float(m[h < a].sum())
    total = h + a
    o15, o25, o35 = (float(m[total > k].sum()) for k in (1, 2, 3))
    btts = float(m[1:, 1:].sum())
    home = m.sum(axis=1)  # ev sahibinin gol dağılımı
    away = m.sum(axis=0)
    h05, h15 = float(home[1:].sum()), float(home[2:].sum())
    a05, a15 = float(away[1:].sum()), float(away[2:].sum())
    return {
        "MS1": p1, "MSX": px, "MS2": p2,
        "CS1X": p1 + px, "CS12": p1 + p2, "CSX2": px + p2,
        "UST15": o15, "ALT15": 1.0 - o15,
        "UST25": o25, "ALT25": 1.0 - o25,
        "UST35": o35, "ALT35": 1.0 - o35,
        "KGVAR": btts, "KGYOK": 1.0 - btts,
        "EVUST05": h05, "EVALT05": 1.0 - h05, "EVUST15": h15, "EVALT15": 1.0 - h15,
        "DEPUST05": a05, "DEPALT05": 1.0 - a05, "DEPUST15": a15, "DEPALT15": 1.0 - a15,
    }


def probs_from_1x2(p1: float, px: float, p2: float, base: dict[str, float]) -> dict[str, float]:
    out = dict(base)
    out.update({"MS1": p1, "MSX": px, "MS2": p2, "CS1X": p1 + px, "CS12": p1 + p2, "CSX2": px + p2})
    return out


def top_scores(m: np.ndarray, n: int = 5) -> list[tuple[int, int, float]]:
    flat = np.argsort(m, axis=None)[::-1][:n]
    rows, cols = np.unravel_index(flat, m.shape)
    return [(int(r), int(c), float(m[r, c])) for r, c in zip(rows, cols)]


def pick_wins(market: str, home_goals: int, away_goals: int) -> bool:
    h, a = home_goals, away_goals
    rules = {
        "MS1": h > a, "MSX": h == a, "MS2": h < a,
        "CS1X": h >= a, "CS12": h != a, "CSX2": h <= a,
        "UST15": h + a > 1, "ALT15": h + a <= 1,
        "UST25": h + a > 2, "ALT25": h + a <= 2,
        "UST35": h + a > 3, "ALT35": h + a <= 3,
        "KGVAR": h > 0 and a > 0, "KGYOK": h == 0 or a == 0,
        "EVUST05": h > 0, "EVALT05": h == 0, "EVUST15": h > 1, "EVALT15": h <= 1,
        "DEPUST05": a > 0, "DEPALT05": a == 0, "DEPUST15": a > 1, "DEPALT15": a <= 1,
    }
    if market not in rules:
        raise ValueError(f"bilinmeyen pazar: {market}")
    return bool(rules[market])
