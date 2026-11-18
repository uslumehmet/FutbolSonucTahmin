"""Milli maçlar için Elo farkına dayalı Poisson gol modeli."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor


@dataclass
class IntlGoalModel:
    intercept: float
    coef_diff: float
    coef_home: float

    def expected_goals(self, elo_home: float, elo_away: float, neutral: bool) -> tuple[float, float]:
        diff = (elo_home - elo_away) / 400.0
        venue = 0.0 if neutral else 1.0
        lam = math.exp(self.intercept + self.coef_diff * diff + self.coef_home * venue)
        mu = math.exp(self.intercept - self.coef_diff * diff - self.coef_home * venue)
        return lam, mu


def fit_intl_goal_model(df: pd.DataFrame, since: datetime) -> IntlGoalModel:
    """df: elo_home, elo_away, neutral, home_goals, away_goals, tier, kickoff_utc. Hazırlık maçları hariç."""
    data = df[(pd.to_datetime(df["kickoff_utc"], utc=True) >= pd.Timestamp(since))
              & (df["tier"] != "FRIENDLY")].dropna(subset=["home_goals", "away_goals"])
    diff = ((data["elo_home"] - data["elo_away"]) / 400.0).to_numpy()
    venue = np.where(data["neutral"].astype(bool).to_numpy(), 0.0, 1.0)
    X = np.vstack([np.column_stack([diff, venue]), np.column_stack([-diff, -venue])])
    y = np.concatenate([data["home_goals"].to_numpy(dtype=float), data["away_goals"].to_numpy(dtype=float)])
    reg = PoissonRegressor(alpha=1e-6, max_iter=300).fit(X, y)
    return IntlGoalModel(intercept=float(reg.intercept_), coef_diff=float(reg.coef_[0]), coef_home=float(reg.coef_[1]))
