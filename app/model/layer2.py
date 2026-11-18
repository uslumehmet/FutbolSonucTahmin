"""Katman 2: Katman 1 olasılıklarını form özellikleriyle düzelten makine öğrenmesi modeli.

Bahis oranları özellik olarak KULLANILMAZ; modelin piyasadan bağımsız görüşü olmalı.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.model.features import FEATURE_COLUMNS

GROUPS = {"1X2": ["MS1", "MSX", "MS2"], "OU25": ["UST25"], "BTTS": ["KGVAR"]}
EPS = 1e-6
ECE_CEILING = 0.05


def outcomes_for(group: str, home_goals: pd.Series, away_goals: pd.Series) -> pd.Series:
    hg, ag = home_goals.astype(int), away_goals.astype(int)
    if group == "1X2":
        return pd.Series(np.where(hg > ag, 0, np.where(hg == ag, 1, 2)), index=hg.index)
    if group == "OU25":
        return ((hg + ag) > 2).astype(int)
    if group == "BTTS":
        return ((hg > 0) & (ag > 0)).astype(int)
    raise ValueError(group)


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def design_matrix(features: pd.DataFrame, l1: pd.DataFrame, group: str) -> pd.DataFrame:
    X = features[FEATURE_COLUMNS].reset_index(drop=True).astype(float).copy()
    for col in GROUPS[group]:
        X[f"l1_{col}"] = _logit(l1[col].to_numpy())
    return X


def l1_matrix(l1: pd.DataFrame, group: str) -> np.ndarray:
    if group == "1X2":
        p = l1[GROUPS[group]].to_numpy(dtype=float)
        return p / p.sum(axis=1, keepdims=True)
    return l1[GROUPS[group][0]].to_numpy(dtype=float)


@dataclass
class Layer2Model:
    group: str
    kind: str
    estimator: object

    def predict(self, features: pd.DataFrame, l1: pd.DataFrame) -> np.ndarray:
        proba = self.estimator.predict_proba(design_matrix(features, l1, self.group))
        return proba if self.group == "1X2" else proba[:, 1]


def log_loss_and_ece(probs: np.ndarray, y: pd.Series, group: str) -> tuple[float, float]:
    y = np.asarray(y, dtype=int)
    if group == "1X2":
        p = np.clip(probs, EPS, 1)
        p = p / p.sum(axis=1, keepdims=True)
        ll = float(-np.mean(np.log(p[np.arange(len(y)), y])))
        ece = float(np.mean([_ece(p[:, k], (y == k).astype(int)) for k in range(3)]))
        return ll, ece
    p = np.clip(np.asarray(probs, dtype=float), EPS, 1 - EPS)
    ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    return ll, _ece(p, y)


def _ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            total += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(total)


def should_keep(l1_ll: float, l1_ece: float, l2_ll: float, l2_ece: float) -> bool:
    # Sabit bir tahmincinin ECE'si yapay olarak çok düşüktür; bu yüzden mutlak bir tavan da kabul edilir.
    return bool(l2_ll < l1_ll - 0.001 and l2_ece <= max(l1_ece + 0.005, ECE_CEILING))


def _candidates():
    logreg = make_pipeline(SimpleImputer(add_indicator=True), StandardScaler(),
                           LogisticRegression(C=1.0, max_iter=2000))
    hgb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, early_stopping=True,
                                         random_state=0)
    return {"logreg": logreg, "hgb": hgb}


def _calibrated(est):
    return CalibratedClassifierCV(est, method="isotonic", cv=TimeSeriesSplit(n_splits=3))


def evaluate_and_train(features: pd.DataFrame, l1: pd.DataFrame, y: pd.Series, group: str
                       ) -> tuple[Layer2Model | None, dict]:
    """Girdi zaman sırasında olmalı. Son %20'de Katman 1 ile kıyaslar; iyileştirmezse None döner."""
    X = design_matrix(features, l1, group)
    y = pd.Series(np.asarray(y, dtype=int))
    cut = int(len(X) * 0.8)
    X_tr, X_te, y_tr, y_te = X.iloc[:cut], X.iloc[cut:], y.iloc[:cut], y.iloc[cut:]
    l1_te = l1_matrix(l1.iloc[cut:].reset_index(drop=True), group)
    l1_ll, l1_ece = log_loss_and_ece(l1_te, y_te, group)
    best = None
    for kind, est in _candidates().items():
        model = _calibrated(est).fit(X_tr, y_tr)
        proba = model.predict_proba(X_te)
        proba = proba if group == "1X2" else proba[:, 1]
        ll, ece = log_loss_and_ece(proba, y_te, group)
        if best is None or ll < best[1]:
            best = (kind, ll, ece)
    kind, l2_ll, l2_ece = best
    metrics = {"group": group, "kind": kind, "n": int(len(X)), "l1_ll": l1_ll, "l1_ece": l1_ece,
               "l2_ll": l2_ll, "l2_ece": l2_ece}
    keep = should_keep(l1_ll, l1_ece, l2_ll, l2_ece)
    metrics["kept"] = keep
    if not keep:
        return None, metrics
    final = _calibrated(_candidates()[kind]).fit(X, y)
    return Layer2Model(group=group, kind=kind, estimator=final), metrics


def save_layer2(models: dict[str, Layer2Model | None], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, path)


def load_layer2(path: Path) -> dict[str, Layer2Model | None]:
    if not Path(path).exists():
        return {g: None for g in GROUPS}
    return joblib.load(path)


LAYER2_BLOB_KEY = "layer2"


def save_layer2_db(session, models: dict[str, Layer2Model | None]) -> None:
    """Modeli veritabanına yazar: bulut sunucusu ve GitHub Actions aynı modeli kullanır."""
    from app.models import ModelBlob, utcnow

    buf = io.BytesIO()
    joblib.dump(models, buf)
    row = session.get(ModelBlob, LAYER2_BLOB_KEY)
    if row is None:
        session.add(ModelBlob(key=LAYER2_BLOB_KEY, data=buf.getvalue(), updated_at=utcnow()))
    else:
        row.data, row.updated_at = buf.getvalue(), utcnow()
    session.commit()


def load_layer2_db(session) -> dict[str, Layer2Model | None] | None:
    from app.models import ModelBlob

    row = session.get(ModelBlob, LAYER2_BLOB_KEY)
    return None if row is None else joblib.load(io.BytesIO(row.data))
