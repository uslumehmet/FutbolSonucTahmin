"""Tahmin için kısa Türkçe gerekçe metinleri (şablon tabanlı)."""
from __future__ import annotations

from app.model.features import RecentMatch
from app.model.injuries import MissingImpact

_PTS = {"G": 3, "B": 1, "M": 0}


def _form_line(team: str, form: list[RecentMatch]) -> str | None:
    if len(form) < 5:
        return None
    pts = sum(_PTS[r.result] for r in form[:5])
    if pts >= 10:
        return f"{team} son 5 maçta {pts} puan topladı (iyi form)"
    if pts <= 4:
        return f"{team} son 5 maçta {pts} puan topladı (kötü form)"
    return None


def explain(home: str, away: str, lam_h: float, lam_a: float, home_form: list[RecentMatch],
            away_form: list[RecentMatch], h2h: list[RecentMatch], missing: list[tuple[str, str, MissingImpact]],
            elo_diff: float | None) -> list[str]:
    lines = [f"Gol beklentisi: {home} {lam_h:.2f} – {lam_a:.2f} {away}"]
    for team, form in ((home, home_form), (away, away_form)):
        line = _form_line(team, form)
        if line:
            lines.append(line)
    for team, player, imp in missing:
        if imp.attack_effect >= 0.05:
            lines.append(f"{team}: {player} eksik (hücuma etkisi yüksek)")
        elif imp.concede_effect >= 0.03:
            lines.append(f"{team}: {player} eksik (savunmaya etkisi)")
    if len(h2h) >= 3:
        w = sum(r.result == "G" for r in h2h)
        d = sum(r.result == "B" for r in h2h)
        lo = sum(r.result == "M" for r in h2h)
        lines.append(f"Aralarındaki son {len(h2h)} maç: {w} galibiyet, {d} beraberlik, {lo} mağlubiyet ({home} açısından)")
    if elo_diff is not None:
        if elo_diff >= 150:
            lines.append(f"{home} güç olarak belirgin önde")
        elif elo_diff <= -150:
            lines.append(f"{away} güç olarak belirgin önde")
    return lines
