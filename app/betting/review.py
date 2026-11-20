"""Kupondaki bir seçimin özeti: tutması için ne gerekiyordu, maçta ne oldu, model maçtan önce ne bekliyordu.

Kuponlarım sayfasında maça tıklayınca açılan kutu bu metinleri gösterir. Metinler şablonla kurulur;
maç istatistiği (şut, top hâkimiyeti) elimizde olmadığı için açıklama skor ve gol beklentisi üzerinden yapılır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Prediction

_GOALS = re.compile(r"^(EV|DEP)?(UST|ALT)(\d)5$")
_RESULT_TEXT = {"MS1": "{home} kazanır", "MSX": "beraberlik", "MS2": "{away} kazanır"}


@dataclass
class PickReview:
    needed: str
    happened: str | None = None
    why: str | None = None
    lines: list[str] = field(default_factory=list)


def _pct(p: float) -> str:
    return f"%{round(p * 100)}"


def condition(key: str, home: str, away: str) -> str:
    """Seçimin tutması için maçta ne olması gerektiği."""
    g = _GOALS.match(key)
    if g:
        side, kind, n = g.group(1), g.group(2), int(g.group(3))
        if side is None:
            return f"maçta toplam en az {n + 1} gol olmalı" if kind == "UST" else f"maçta toplam en fazla {n} gol olmalı"
        team = home if side == "EV" else away
        if kind == "UST":
            return f"{team} en az {n + 1} gol atmalı"
        return f"{team} gol atmamalı" if n == 0 else f"{team} en fazla {n} gol atmalı"
    return {
        "MS1": f"{home} kazanmalı", "MSX": "maç berabere bitmeli", "MS2": f"{away} kazanmalı",
        "CS1X": f"{home} kaybetmemeli (kazanır ya da berabere)", "CS12": "maç berabere bitmemeli",
        "CSX2": f"{away} kaybetmemeli (kazanır ya da berabere)",
        "KGVAR": "iki takım da gol atmalı", "KGYOK": "takımlardan en az biri gol atmamalı",
    }.get(key, key)


def _happened(key: str, hg: int, ag: int, home: str, away: str) -> str:
    head = f"Maç {hg}–{ag} bitti"
    g = _GOALS.match(key)
    if g and g.group(1) is None:
        return f"{head}; toplam {hg + ag} gol oldu."
    if g:
        team, goals = (home, hg) if g.group(1) == "EV" else (away, ag)
        return f"{head}; {team} gol atamadı." if goals == 0 else f"{head}; {team} {goals} gol attı."
    if key.startswith("KG"):
        if hg and ag:
            return f"{head}; iki takım da gol attı."
        if not hg and not ag:
            return f"{head}; iki takım da gol atamadı."
        return f"{head}; {home if not hg else away} gol atamadı."
    if hg > ag:
        return f"{head}; {home} kazandı."
    if hg < ag:
        return f"{head}; {away} kazandı."
    return f"{head}; berabere bitti."


def _why(key: str, result: str, hg: int, ag: int, home: str, away: str, pred) -> str | None:
    """Model maçtan önce ne bekliyordu, maçta ne oldu. Tahmin yoksa None."""
    if pred is None:
        return None
    g = _GOALS.match(key)
    lh, la = pred.lam_home, pred.lam_away
    if g and lh is not None and la is not None:
        side, kind = g.group(1), g.group(2)
        if side is None:
            exp, real = lh + la, hg + ag
            text = f"Model maçtan önce toplam {exp:.1f} gol bekliyordu, {real} gol oldu."
            if result == "lost":
                text += " Beklenenden çok gol oldu." if kind == "ALT" else " Beklenenden az gol oldu."
            return text
        team, exp, real = (home, lh, hg) if side == "EV" else (away, la, ag)
        text = f"Model maçtan önce {team} için {exp:.1f} gol bekliyordu, {real} attı."
        if result == "lost":
            text += f" {team} beklenenden fazla gol attı." if kind == "ALT" else f" {team} beklenenden az gol attı."
        return text
    probs = pred.probs or {}
    if key.startswith("KG"):
        actual = "KGVAR" if hg and ag else "KGYOK"
        what = "iki takımın da gol atması" if actual == "KGVAR" else "takımlardan birinin gol atamaması"
        if actual in probs:
            return f"Model maçtan önce {what} ihtimaline {_pct(probs[actual])} diyordu."
        return None
    actual = "MS1" if hg > ag else "MS2" if hg < ag else "MSX"
    if actual in probs:
        label = _RESULT_TEXT[actual].format(home=home, away=away)
        return f"Model maçtan önce bu sonuca ({label}) {_pct(probs[actual])} ihtimal veriyordu."
    return None


def _chance_line(prob: float, odds: float | None) -> str:
    text = f"Model bu seçime {_pct(prob)} dedi"
    if odds:
        text += f"; {odds:.2f} oranı yaklaşık {_pct(1 / odds)} demek"
    return text + "."


def _failure_line(prob: float) -> str:
    miss = round((1 - prob) * 10)
    if miss >= 1:
        return f"{_pct(prob)} ihtimal, her 10 seçimden yaklaşık {miss} tanesi tutmaz demek; tek bir maçta bu olabilir."
    return (f"{_pct(prob)} ihtimal, her 100 seçimden yaklaşık {round((1 - prob) * 100)} tanesi tutmaz demek; "
            "tek bir maçta bu olabilir.")


def review_pick(market: str, prob: float, odds: float | None, result: str, status: str, hg: int | None,
                ag: int | None, home: str, away: str, pred, stats: dict | None = None) -> PickReview:
    r = PickReview(needed=condition(market, home, away))
    finished = status == "finished" and hg is not None and ag is not None
    if result == "void":
        r.happened = "Maç ertelendi ya da iptal edildi; seçim iptal sayıldı (oranı 1)."
    elif finished:
        r.happened = _happened(market, hg, ag, home, away)
        r.why = _why(market, result, hg, ag, home, away, pred)
    if pred is not None and pred.lam_home is not None and pred.lam_away is not None:
        r.lines.append(f"Gol beklentisi: {home} {pred.lam_home:.2f} – {pred.lam_away:.2f} {away} "
                       f"(toplam {pred.lam_home + pred.lam_away:.1f})")
    if pred is not None and pred.top_scores:
        top = ", ".join(f"{h}–{a} ({_pct(p)})" for h, a, p in pred.top_scores[:3])
        r.lines.append(f"Modelin en olası skorları: {top}")
    r.lines.append(_chance_line(prob, odds))
    if result == "lost":
        r.lines.append(_failure_line(prob))
    if finished and stats:
        line = stats_line(stats, home, away)
        if line:
            r.lines.insert(0, line)
    return r


def stats_line(stats: dict, home: str, away: str) -> str | None:
    """"Maç istatistiği: şut Wales 9 – 24 Portugal, isabetli şut 4 – 6, topla oynama %38 – %62"."""
    parts = []
    if stats.get("shots"):
        parts.append(f"şut {home} {stats['shots'][0]:.0f} – {stats['shots'][1]:.0f} {away}")
    if stats.get("sot"):
        parts.append(f"isabetli şut {stats['sot'][0]:.0f} – {stats['sot'][1]:.0f}")
    if stats.get("poss"):
        parts.append(f"topla oynama %{stats['poss'][0]:.0f} – %{stats['poss'][1]:.0f}")
    return "Maç istatistiği: " + ", ".join(parts) if parts else None


def reviews_for(session, picks) -> dict[int, PickReview]:
    """Seçimlerin özetleri, seçim id'sine göre. Her seçimde kupon kurulduğu andaki son tahmin kullanılır."""
    match_ids = {p.match_id for p in picks}
    if not match_ids:
        return {}
    by_match: dict[int, list] = {}
    for pr in (session.query(Prediction).filter(Prediction.match_id.in_(match_ids), Prediction.kind == "live")
               .order_by(Prediction.created_at)):
        by_match.setdefault(pr.match_id, []).append(pr)
    out = {}
    for p in picks:
        m = p.match
        preds = by_match.get(p.match_id, [])
        made = p.coupon.created_at if p.coupon is not None else None
        before = [x for x in preds if made is not None and x.created_at <= made] \
            or [x for x in preds if x.created_at <= m.kickoff_utc]
        pred = before[-1] if before else None
        out[p.id] = review_pick(p.market, p.prob, p.odds, p.result, m.status, m.home_goals, m.away_goals,
                                m.home_team.name, m.away_team.name, pred, m.stats or {})
    return out
