"""Eksik (sakat/cezalı) oyuncuların gol beklentisine etkisi.

Başlangıç kuralları küçük katsayılarla çalışır; yeterli veri birikince Katman 2'de öğrenilen
etkiyle güncellenebilir.
"""
from __future__ import annotations

from dataclasses import dataclass

ATTACK_COEF = 0.35
DEF_COEF = 0.03
GK_COEF = 0.06
MAX_EFFECT = 0.15

ROLES = ("hücum", "orta saha", "savunma", "kaleci")
LEVELS = {"düşük": (0.02, 0.01), "orta": (0.05, 0.03), "yüksek": (0.10, 0.06)}


@dataclass(frozen=True)
class MissingImpact:
    attack_effect: float   # takımın kendi gol beklentisini düşürme oranı
    concede_effect: float  # rakibin gol beklentisini artırma oranı


def player_impact(position: str, minutes: int, goals: int, assists: int, team_games: int,
                  team_goals: int) -> MissingImpact:
    if team_games <= 0:
        return MissingImpact(0.0, 0.0)
    minutes_share = min(1.0, minutes / (team_games * 90))
    attack_share = min(1.0, (goals + 0.5 * assists) / max(team_goals, 1))
    coef = GK_COEF if position == "G" else DEF_COEF if position == "D" else 0.0
    return MissingImpact(ATTACK_COEF * attack_share, minutes_share * coef)


def manual_impact(role: str, level: str) -> MissingImpact:
    a, c = LEVELS[level]
    if role == "hücum":
        return MissingImpact(a, 0.0)
    if role == "orta saha":
        return MissingImpact(a / 2, c / 2)
    if role == "savunma":
        return MissingImpact(0.0, c)
    if role == "kaleci":
        return MissingImpact(0.0, 2 * c)
    raise ValueError(f"bilinmeyen rol: {role}")


def adjust_lambdas(lam_h: float, lam_a: float, home_missing: list[MissingImpact],
                   away_missing: list[MissingImpact]) -> tuple[float, float]:
    h_att = min(sum(m.attack_effect for m in home_missing), MAX_EFFECT)
    a_att = min(sum(m.attack_effect for m in away_missing), MAX_EFFECT)
    h_con = min(sum(m.concede_effect for m in home_missing), MAX_EFFECT)
    a_con = min(sum(m.concede_effect for m in away_missing), MAX_EFFECT)
    if not (h_att or a_att or h_con or a_con):
        return lam_h, lam_a
    return lam_h * (1 - h_att) * (1 + a_con), lam_a * (1 - a_att) * (1 + h_con)
