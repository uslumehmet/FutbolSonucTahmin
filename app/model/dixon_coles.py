"""Zaman ağırlıklı Dixon-Coles gol modeli (Katman 1, kulüpler)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize

RIDGE = 0.01
SUM_PENALTY = 100.0


@dataclass
class DCModel:
    attack: dict[int, float]
    defence: dict[int, float]
    home_adv: float
    rho: float
    prior_attack: float
    prior_defence: float
    n_matches: dict[int, int] = field(default_factory=dict)

    def expected_goals(self, home_id: int, away_id: int, neutral: bool = False) -> tuple[float, float]:
        att_h = self.attack.get(home_id, self.prior_attack)
        def_h = self.defence.get(home_id, self.prior_defence)
        att_a = self.attack.get(away_id, self.prior_attack)
        def_a = self.defence.get(away_id, self.prior_defence)
        lam = float(np.exp(att_h + def_a + self.home_adv * (0 if neutral else 1)))
        mu = float(np.exp(att_a + def_h))
        return lam, mu


def _neg_loglik(theta, hi, ai, x, y, home_flag, w, n):
    att, dfn = theta[:n], theta[n:2 * n]
    h, rho = theta[2 * n], theta[2 * n + 1]
    lam = np.exp(att[hi] + dfn[ai] + h * home_flag)
    mu = np.exp(att[ai] + dfn[hi])

    tau = np.ones_like(lam)
    s_lam_tau = np.zeros_like(lam)
    s_mu_tau = np.zeros_like(lam)
    d_rho = np.zeros_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    tau[m00] = 1 - lam[m00] * mu[m00] * rho
    tau[m01] = 1 + lam[m01] * rho
    tau[m10] = 1 + mu[m10] * rho
    tau[m11] = 1 - rho
    tau = np.clip(tau, 1e-10, None)
    s_lam_tau[m00] = -lam[m00] * mu[m00] * rho / tau[m00]
    s_mu_tau[m00] = -lam[m00] * mu[m00] * rho / tau[m00]
    d_rho[m00] = -lam[m00] * mu[m00] / tau[m00]
    s_lam_tau[m01] = lam[m01] * rho / tau[m01]
    d_rho[m01] = lam[m01] / tau[m01]
    s_mu_tau[m10] = mu[m10] * rho / tau[m10]
    d_rho[m10] = mu[m10] / tau[m10]
    d_rho[m11] = -1 / tau[m11]

    ll = w * (x * np.log(lam) - lam + y * np.log(mu) - mu + np.log(tau))
    s_lam = w * (x - lam + s_lam_tau)
    s_mu = w * (y - mu + s_mu_tau)

    g_att = np.zeros(n)
    g_def = np.zeros(n)
    np.add.at(g_att, hi, s_lam)
    np.add.at(g_def, ai, s_lam)
    np.add.at(g_att, ai, s_mu)
    np.add.at(g_def, hi, s_mu)
    g_h = float((s_lam * home_flag).sum())
    g_rho = float((w * d_rho).sum())

    total = att.sum()
    obj = -ll.sum() + SUM_PENALTY * total ** 2 + RIDGE * (att @ att + dfn @ dfn)
    grad = np.concatenate([
        -g_att + 2 * SUM_PENALTY * total + 2 * RIDGE * att,
        -g_def + 2 * RIDGE * dfn,
        [-g_h, -g_rho],
    ])
    return obj, grad


def fit_dixon_coles(df: pd.DataFrame, ref_time: datetime, half_life_days: float = 365.0,
                    init: DCModel | None = None, neutral_col: str | None = None) -> DCModel:
    data = df[df["kickoff_utc"] < ref_time]
    data = data.dropna(subset=["home_goals", "away_goals"])
    teams = sorted(set(data["home_id"]).union(data["away_id"]))
    if len(teams) < 2:
        raise ValueError("Dixon-Coles için yeterli maç yok")
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = data["home_id"].map(idx).to_numpy()
    ai = data["away_id"].map(idx).to_numpy()
    x = data["home_goals"].to_numpy(dtype=float)
    y = data["away_goals"].to_numpy(dtype=float)
    home_flag = np.ones(len(data))
    if neutral_col and neutral_col in data:
        home_flag = 1.0 - data[neutral_col].astype(float).to_numpy()
    ref = pd.Timestamp(ref_time)
    age = (ref - pd.to_datetime(data["kickoff_utc"], utc=True)).dt.total_seconds().to_numpy() / 86400.0
    w = np.power(0.5, age / half_life_days)

    theta0 = np.zeros(2 * n + 2)
    theta0[2 * n], theta0[2 * n + 1] = 0.25, -0.05
    if init is not None:
        for t, i in idx.items():
            theta0[i] = init.attack.get(t, init.prior_attack)
            theta0[n + i] = init.defence.get(t, init.prior_defence)
        theta0[2 * n], theta0[2 * n + 1] = init.home_adv, init.rho
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.2, 0.2)]
    res = minimize(_neg_loglik, theta0, args=(hi, ai, x, y, home_flag, w, n), jac=True,
                   method="L-BFGS-B", bounds=bounds, options={"maxiter": 500})
    th = res.x
    att = {t: float(th[i]) for t, i in idx.items()}
    dfn = {t: float(th[n + i]) for t, i in idx.items()}
    counts = pd.concat([data["home_id"], data["away_id"]]).value_counts()
    return DCModel(
        attack=att, defence=dfn, home_adv=float(th[2 * n]), rho=float(th[2 * n + 1]),
        prior_attack=float(np.quantile(list(att.values()), 0.25)),
        prior_defence=float(np.quantile(list(dfn.values()), 0.75)),
        n_matches={int(k): int(v) for k, v in counts.items()},
    )
