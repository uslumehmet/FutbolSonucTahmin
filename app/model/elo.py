"""Elo reytingleri (kulüp ve milli takımlar)."""
from __future__ import annotations

import math
from typing import Callable

import pandas as pd

K_BY_TIER = {"WC": 60, "CONT": 50, "EURO": 50, "WCQ": 40, "EUROQ": 40, "QUAL": 40, "UNL": 40,
             "OTHER": 30, "FRIENDLY": 20}
CLUB_K = 20
CLUB_HOME_ADV = 60
NATIONAL_HOME_ADV = 100


def goal_diff_multiplier(gd: int) -> float:
    gd = abs(int(gd))
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11 + gd) / 8


class EloTable:
    def __init__(self, home_adv: float, base: float = 1500.0):
        self.home_adv = home_adv
        self.base = base
        self.ratings: dict[int, float] = {}

    def rating(self, team_id: int) -> float:
        return self.ratings.get(team_id, self.base)

    def expected_home(self, home: int, away: int, neutral: bool) -> float:
        diff = self.rating(home) + (0 if neutral else self.home_adv) - self.rating(away)
        return 1.0 / (1.0 + math.pow(10.0, -diff / 400.0))

    def update(self, home: int, away: int, hg: int, ag: int, k: float, neutral: bool) -> None:
        exp = self.expected_home(home, away, neutral)
        w = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        delta = k * goal_diff_multiplier(hg - ag) * (w - exp)
        self.ratings[home] = self.rating(home) + delta
        self.ratings[away] = self.rating(away) - delta


def elo_history(df: pd.DataFrame, home_adv: float, k_fn: Callable[[pd.Series], float]) -> tuple[pd.DataFrame, EloTable]:
    """Her satıra maç öncesi `elo_home`/`elo_away` ekler; oynanmamış satırlar reytingi değiştirmez."""
    data = df.sort_values("kickoff_utc", kind="stable").copy()
    table = EloTable(home_adv)
    pre_h, pre_a = [], []
    neutral = data["neutral"] if "neutral" in data else pd.Series(False, index=data.index)
    for (_, row), neu in zip(data.iterrows(), neutral):
        h, a = int(row["home_id"]), int(row["away_id"])
        pre_h.append(table.rating(h))
        pre_a.append(table.rating(a))
        if pd.notna(row["home_goals"]) and pd.notna(row["away_goals"]):
            table.update(h, a, int(row["home_goals"]), int(row["away_goals"]), k_fn(row), bool(neu))
    data["elo_home"] = pre_h
    data["elo_away"] = pre_a
    return data, table
