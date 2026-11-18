from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.model.elo import EloTable, elo_history, goal_diff_multiplier
from app.model.international import fit_intl_goal_model


def test_goal_diff_multiplier():
    assert [goal_diff_multiplier(g) for g in (0, 1, 2, 3, 5)] == [1, 1, 1.5, 1.75, 2.0]
    assert goal_diff_multiplier(-3) == 1.75


def test_update_zero_sum_and_direction():
    t = EloTable(home_adv=60)
    t.update(1, 2, 2, 0, k=20, neutral=False)
    assert t.rating(1) > 1500 > t.rating(2)
    assert t.rating(1) - 1500 == pytest.approx(1500 - t.rating(2))


def test_expected_home_advantage():
    t = EloTable(home_adv=100)
    assert t.expected_home(1, 2, neutral=False) > 0.5
    assert t.expected_home(1, 2, neutral=True) == 0.5


def test_elo_history_is_pre_match():
    df = pd.DataFrame({"home_id": [1, 1], "away_id": [2, 2], "home_goals": [3, 0], "away_goals": [0, 0],
                       "kickoff_utc": [datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)],
                       "neutral": [False, False]})
    out, table = elo_history(df, home_adv=60, k_fn=lambda r: 20)
    assert out.iloc[0].elo_home == 1500 and out.iloc[0].elo_away == 1500
    assert out.iloc[1].elo_home > 1500


def test_unplayed_rows_get_ratings_without_update():
    df = pd.DataFrame({"home_id": [1, 1], "away_id": [2, 2], "home_goals": [3, np.nan], "away_goals": [0, np.nan],
                       "kickoff_utc": [datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)],
                       "neutral": [False, False]})
    out, table = elo_history(df, home_adv=60, k_fn=lambda r: 20)
    assert out.iloc[1].elo_home == pytest.approx(table.rating(1))


def test_intl_model_stronger_scores_more():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(3000):
        eh, ea = rng.normal(1600, 200), rng.normal(1600, 200)
        diff = (eh - ea) / 400
        rows.append({"elo_home": eh, "elo_away": ea, "neutral": bool(i % 3 == 0),
                     "home_goals": rng.poisson(np.exp(0.2 + 0.8 * diff + (0 if i % 3 == 0 else 0.15))),
                     "away_goals": rng.poisson(np.exp(0.2 - 0.8 * diff - (0 if i % 3 == 0 else 0.15))),
                     "tier": "WCQ", "kickoff_utc": datetime(2015, 1, 1, tzinfo=UTC) + timedelta(days=i)})
    m = fit_intl_goal_model(pd.DataFrame(rows), since=datetime(2010, 1, 1, tzinfo=UTC))
    assert m.coef_diff == pytest.approx(0.8, abs=0.1)
    lam, mu = m.expected_goals(1900, 1500, neutral=True)
    assert lam > mu
    assert m.expected_goals(1600, 1600, neutral=False)[0] > m.expected_goals(1600, 1600, neutral=True)[0]
