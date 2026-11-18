"""Mod, tekli seçim ve kalibrasyon istatistikleri."""
from __future__ import annotations

import numpy as np

from app.model.markets import MARKET_LABELS
from app.models import Competition, Coupon, Match, Prediction, SinglePick


def _unit(rows: list[tuple[str, float]]) -> dict:
    settled = [(r, o) for r, o in rows if r in ("won", "lost")]
    n = len(settled)
    won = sum(1 for r, _ in settled if r == "won")
    ret = sum(o for r, o in settled if r == "won")
    return {"coupons": n, "won": won, "hit": won / n if n else None, "unit_roi": (ret - n) / n if n else None}


def mode_stats(session, mode: str) -> dict:
    coupons = session.query(Coupon).filter_by(mode=mode, source="auto").all()
    out = _unit([(c.status, c.settled_odds or 0.0) for c in coupons])
    out["open"] = sum(1 for c in coupons if c.status == "open")
    by_target: dict[float, list] = {}
    for c in coupons:
        by_target.setdefault(float(c.target_odds), []).append((c.status, c.settled_odds or 0.0))
    out["by_target"] = {t: _unit(rows) for t, rows in sorted(by_target.items())}
    return out


def singles_stats(session, mode: str, by: str | None = None) -> list[dict]:
    q = session.query(SinglePick).filter(SinglePick.mode == mode, SinglePick.result.in_(("won", "lost")))
    comps = {c.id: c.name for c in session.query(Competition)}
    picks = q.all()
    match_comp = {}
    if by == "league" and picks:
        match_comp = dict(session.query(Match.id, Match.competition_id)
                          .filter(Match.id.in_({sp.match_id for sp in picks})).all())
    groups: dict[str, list] = {}
    for sp in picks:
        if by == "market":
            key = MARKET_LABELS.get(sp.market, sp.market)
        elif by == "league":
            key = comps.get(match_comp.get(sp.match_id), "?")
        else:
            key = "Toplam"
        groups.setdefault(key, []).append((sp.result, sp.odds))
    rows = []
    for key, items in groups.items():
        u = _unit(items)
        rows.append({"key": key, "n": u["coupons"], "won": u["won"], "hit": u["hit"], "unit_roi": u["unit_roi"]})
    return sorted(rows, key=lambda r: -r["n"])


_GROUP_MARKET = {"1X2": "MS1", "OU25": "UST25", "BTTS": "KGVAR"}


def calibration_rows(session) -> list[tuple[dict, int, int]]:
    """Biten her maç için başlamadan önceki son canlı tahmin: (ihtimaller, ev golü, deplasman golü).

    Tek sorgu; tahminler sıralı gelir, her maçın en son tahmini kalır.
    """
    q = (session.query(Prediction.match_id, Prediction.probs, Match.home_goals, Match.away_goals)
         .join(Match, Match.id == Prediction.match_id)
         .filter(Prediction.kind == "live", Match.status == "finished",
                 Prediction.created_at < Match.kickoff_utc)
         .order_by(Prediction.match_id, Prediction.created_at, Prediction.id))
    latest: dict[int, tuple[dict, int, int]] = {}
    for mid, probs, hg, ag in q:
        latest[mid] = (probs or {}, hg, ag)
    return [latest[k] for k in sorted(latest)]


def live_calibration(session, group: str, bins: int = 10, rows=None) -> list[list]:
    market = _GROUP_MARKET[group]
    ps, ys = [], []
    for probs, hg, ag in (calibration_rows(session) if rows is None else rows):
        if market not in probs or hg is None or ag is None:
            continue
        y = {"MS1": hg > ag, "UST25": hg + ag > 2, "KGVAR": hg > 0 and ag > 0}[market]
        ps.append(float(probs[market]))
        ys.append(1.0 if y else 0.0)
    if not ps:
        return []
    p, y = np.array(ps), np.array(ys)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    out = []
    for b in range(bins):
        mask = idx == b
        if mask.any():
            out.append([round(float(p[mask].mean()), 4), round(float(y[mask].mean()), 4), int(mask.sum())])
    return out
