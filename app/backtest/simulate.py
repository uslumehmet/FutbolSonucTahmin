"""Geçmişe dönük test raporu (geçmiş oran olmadığı için isabet ve kalibrasyon)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import CLUB_LEAGUES
from app.model.features import FEATURE_COLUMNS
from app.model.layer2 import GROUPS, evaluate_and_train, log_loss_and_ece, outcomes_for
from app.model.markets import MARKETS
from app.models import BacktestRun

PICK_MARKETS = ["MS1", "MSX", "MS2", "UST25", "ALT25", "KGVAR", "KGYOK"]
THRESHOLDS = [0.5, 0.6, 0.7, 0.8]
NOTES = [
    "Geçmiş bahis oranı yok (football-data.co.uk erişime kapalı): kâr/zarar ölçülmedi, yalnızca isabet ve kalibrasyon.",
    "Geçmiş veride eksik oyuncu bilgisi yok; test eksik etkisi olmadan yapıldı.",
]


def _calibration(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[list[float]]:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    out = []
    for b in range(bins):
        mask = idx == b
        if mask.any():
            out.append([round(float(p[mask].mean()), 4), round(float(y[mask].mean()), 4), int(mask.sum())])
    return out


def _apply_l2(probs: pd.DataFrame, group: str, out: np.ndarray, rows: pd.Index) -> None:
    if group == "1X2":
        probs.loc[rows, ["MS1", "MSX", "MS2"]] = out
        probs.loc[rows, "CS1X"] = probs.loc[rows, "MS1"] + probs.loc[rows, "MSX"]
        probs.loc[rows, "CS12"] = probs.loc[rows, "MS1"] + probs.loc[rows, "MS2"]
        probs.loc[rows, "CSX2"] = probs.loc[rows, "MSX"] + probs.loc[rows, "MS2"]
    elif group == "OU25":
        probs.loc[rows, "UST25"], probs.loc[rows, "ALT25"] = out, 1 - out
    else:
        probs.loc[rows, "KGVAR"], probs.loc[rows, "KGYOK"] = out, 1 - out


def simulate(session, seasons: list[int], comp_codes: list[str] | None = None, train_layer2: bool = True) -> dict:
    from app.backtest.walkforward import training_frame

    df = training_frame(session, comp_codes or [lg.code for lg in CLUB_LEAGUES])
    test = df[df["season"].isin(seasons)].copy()
    probs = test[MARKETS].astype(float).copy()
    layer2_info: dict[str, dict] = {}
    if train_layer2:
        for season in sorted(seasons):
            rows = test.index[test["season"] == season]
            if not len(rows):
                continue
            start = test.loc[rows, "kickoff_utc"].min()
            train = df[df["kickoff_utc"] < start]
            for group in GROUPS:
                feats = train[FEATURE_COLUMNS].copy()
                feats.attrs["max_kickoff"] = train["kickoff_utc"].max()
                y = outcomes_for(group, train["home_goals"], train["away_goals"])
                model, metrics = evaluate_and_train(feats, train[MARKETS].reset_index(drop=True), y, group) \
                    if len(train) else (None, {"kept": False, "reason": "veri yok"})
                layer2_info[group] = {k: v for k, v in metrics.items() if k != "group"}
                if model is not None:
                    out = model.predict(test.loc[rows, FEATURE_COLUMNS], probs.loc[rows, MARKETS])
                    _apply_l2(probs, group, out, rows)
    base = df[df["kickoff_utc"] < test["kickoff_utc"].min()] if len(test) else df
    if base.empty:
        base = test
    groups = {}
    for group, cols in GROUPS.items():
        y = outcomes_for(group, test["home_goals"], test["away_goals"]).to_numpy()
        yb = outcomes_for(group, base["home_goals"], base["away_goals"]).to_numpy()
        if group == "1X2":
            p = probs[["MS1", "MSX", "MS2"]].to_numpy()
            p = p / p.sum(axis=1, keepdims=True)
            rates = np.array([(yb == k).mean() for k in range(3)])
            base_p = np.tile(rates, (len(y), 1))
            acc = float((p.argmax(axis=1) == y).mean()) if len(y) else 0.0
            calib = _calibration(p[:, 0], (y == 0).astype(int))
        else:
            p = probs[cols[0]].to_numpy()
            base_p = np.full(len(y), yb.mean())
            acc = float(((p >= 0.5).astype(int) == y).mean()) if len(y) else 0.0
            calib = _calibration(p, y)
        ll, ece = log_loss_and_ece(p, y, group) if len(y) else (0.0, 0.0)
        bll, _ = log_loss_and_ece(base_p, y, group) if len(y) else (0.0, 0.0)
        groups[group] = {"model_ll": round(ll, 4), "base_ll": round(bll, 4), "ece": round(ece, 4),
                         "accuracy": round(acc, 4), "calibration": calib}
    confidence = []
    if len(test):
        pm = probs[PICK_MARKETS].to_numpy()
        best = pm.argmax(axis=1)
        best_p = pm.max(axis=1)
        from app.model.markets import pick_wins
        hits = np.array([pick_wins(PICK_MARKETS[b], int(h), int(a)) for b, h, a in
                         zip(best, test["home_goals"], test["away_goals"])])
        for thr in THRESHOLDS:
            mask = best_p >= thr
            confidence.append({"min_prob": thr, "n": int(mask.sum()),
                               "avg_prob": round(float(best_p[mask].mean()), 4) if mask.any() else None,
                               "hit": round(float(hits[mask].mean()), 4) if mask.any() else None})
    report = {"seasons": seasons, "n_matches": int(len(test)), "groups": groups, "layer2": layer2_info,
              "confidence": confidence, "notes": NOTES}
    session.add(BacktestRun(report=report))
    session.commit()
    return report
