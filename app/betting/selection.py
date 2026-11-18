"""Seçim: maç başına en mantıklı (Güvenli) ve değerli seçim."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config import Settings
from app.model.markets import MARKET_GROUP, MARKETS
from app.models import Match, OddsQuote


@dataclass(frozen=True)
class Candidate:
    match_id: int
    market: str
    odds: float
    prob: float
    implied: float | None = None  # piyasanın (marjı temizlenmiş) ihtimali
    low_data: bool = False  # tahmin "veri az" işaretli (takımların az maçı var)

    @property
    def value(self) -> float:
        return self.prob * self.odds - 1


def adjust_odds(odds: float, pct: float) -> float:
    return round(max(1.01, odds * (1 + pct / 100)), 2)


def implied_probs(odds: dict[str, float]) -> dict[str, float]:
    groups: dict[str, list[str]] = {}
    for market in odds:
        groups.setdefault(MARKET_GROUP[market], []).append(market)
    out = {}
    for group, markets in groups.items():
        inv = {m: 1 / odds[m] for m in markets}
        target = 2.0 if group == "DC" else 1.0
        total = sum(inv.values())
        complete = len(markets) == (3 if group in ("1X2", "DC") else 2)
        for m, v in inv.items():
            out[m] = v * target / total if complete else v
    return out


def candidates_for(match_id: int, probs: dict[str, float], odds: dict[str, float],
                   odds_adjust_pct: float, low_data: bool = False) -> list[Candidate]:
    implied = implied_probs(odds)
    return [Candidate(match_id, m, adjust_odds(odds[m], odds_adjust_pct), float(probs[m]), implied.get(m), low_data)
            for m in MARKETS if m in probs and m in odds]


def _plausible(c: Candidate, s: Settings) -> bool:
    """Model piyasadan çok daha iyimserse bu büyük ihtimalle model hatasıdır; seçimden çıkar."""
    return c.implied is None or c.prob - c.implied <= s.max_model_gap


def safe_pick(cands: list[Candidate], min_odds: float) -> Candidate | None:
    ok = [c for c in cands if c.odds >= min_odds]
    return max(ok, key=lambda c: (c.prob, c.odds)) if ok else None


def value_pick(cands: list[Candidate], min_prob: float, min_edge: float) -> Candidate | None:
    ok = [c for c in cands if c.prob >= min_prob and c.value >= min_edge]
    return max(ok, key=lambda c: (c.prob, c.value)) if ok else None


def eligible(cands: list[Candidate], mode: str, s: Settings) -> list[Candidate]:
    if mode == "safe":
        return [c for c in cands if c.odds >= s.safe_min_odds and _plausible(c, s)
                and not (s.safe_skip_low_data and c.low_data)]
    if mode == "value":
        return [c for c in cands if c.prob >= s.value_min_prob and c.value >= s.value_min_edge and _plausible(c, s)]
    raise ValueError(mode)


def latest_odds(session, match_id: int) -> tuple[str, dict[str, float]] | None:
    quotes = (session.query(OddsQuote).filter_by(match_id=match_id)
              .order_by(OddsQuote.fetched_at.desc(), OddsQuote.id.desc()).all())
    if not quotes:
        return None
    book = "B365" if any(q.bookmaker == "B365" for q in quotes) else quotes[0].bookmaker
    odds: dict[str, float] = {}
    for q in quotes:
        if q.bookmaker == book and q.market not in odds:
            odds[q.market] = q.odds
    return book, odds


def match_candidates(session, start: datetime, end: datetime, s: Settings) -> dict[int, list[Candidate]]:
    from app.model.predict import latest_prediction

    out: dict[int, list[Candidate]] = {}
    matches = (session.query(Match)
               .filter(Match.status == "scheduled", Match.unmatched.is_(False),
                       Match.kickoff_utc >= start, Match.kickoff_utc < end)
               .order_by(Match.kickoff_utc).all())
    for m in matches:
        pred = latest_prediction(session, m.id)
        lo = latest_odds(session, m.id)
        if pred is None or not pred.probs or lo is None:
            continue
        cands = candidates_for(m.id, pred.probs, lo[1], s.odds_adjust_pct, "veri az" in (pred.flags or []))
        if cands:
            out[m.id] = cands
    return out
