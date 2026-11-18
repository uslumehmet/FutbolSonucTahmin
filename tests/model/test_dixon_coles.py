from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from app.model.dixon_coles import fit_dixon_coles
from tests.helpers import simulate_league

REF = datetime(2030, 1, 1, tzinfo=UTC)


def test_recovers_team_order_and_home_adv():
    df, truth = simulate_league(seed=1, n_teams=10, n_seasons=4)
    m = fit_dixon_coles(df, REF, half_life_days=10_000)
    ids = sorted(truth["attack"])
    rho_att = spearmanr([m.attack[i] for i in ids], [truth["attack"][i] for i in ids]).statistic
    rho_def = spearmanr([m.defence[i] for i in ids], [truth["defence"][i] for i in ids]).statistic
    assert rho_att > 0.8 and rho_def > 0.8
    assert abs(m.home_adv - 0.25) < 0.1


def test_ignores_future_matches():
    df, _ = simulate_league(seed=2, n_teams=6, n_seasons=2)
    cut = df.kickoff_utc.iloc[len(df) // 2]
    a = fit_dixon_coles(df, cut, half_life_days=365)
    extra = df.copy()
    extra.loc[extra.kickoff_utc >= cut, "home_goals"] = 9
    b = fit_dixon_coles(extra, cut, half_life_days=365)
    assert np.allclose([a.attack[i] for i in a.attack], [b.attack[i] for i in a.attack])


def test_recent_form_weighs_more():
    old, _ = simulate_league(seed=3, n_teams=6, n_seasons=2, attack=[-0.6, 0, 0, 0, 0, 0.1])
    new, _ = simulate_league(seed=4, n_teams=6, n_seasons=1, attack=[0.6, 0, 0, 0, 0, 0.1],
                             start=old.kickoff_utc.max() + timedelta(days=30))
    df = pd.concat([old, new], ignore_index=True)
    short = fit_dixon_coles(df, REF, half_life_days=120)
    long = fit_dixon_coles(df, REF, half_life_days=5_000)
    assert short.attack[1] > long.attack[1]


def test_unknown_team_uses_prior():
    df, _ = simulate_league(seed=5, n_teams=6, n_seasons=1)
    m = fit_dixon_coles(df, REF)
    lam, mu = m.expected_goals(999, 1)
    assert np.isfinite(lam) and np.isfinite(mu)
    assert lam == np.exp(m.prior_attack + m.defence[1] + m.home_adv)


def test_neutral_removes_home_adv():
    df, _ = simulate_league(seed=6, n_teams=6, n_seasons=1)
    m = fit_dixon_coles(df, REF)
    assert m.expected_goals(1, 2, neutral=True)[0] < m.expected_goals(1, 2)[0]


def test_n_matches_counts_training_rows():
    df, _ = simulate_league(seed=7, n_teams=4, n_seasons=1)
    m = fit_dixon_coles(df, REF)
    assert m.n_matches[1] == 6
