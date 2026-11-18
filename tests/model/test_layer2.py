import numpy as np
import pandas as pd

from app.model.features import FEATURE_COLUMNS
from app.model.layer2 import evaluate_and_train, load_layer2, outcomes_for, save_layer2, should_keep


def test_should_keep_rule():
    assert should_keep(0.98, 0.02, 0.97, 0.024) is True
    assert should_keep(0.98, 0.02, 0.9795, 0.02) is False
    assert should_keep(0.98, 0.02, 0.95, 0.06) is False
    assert should_keep(0.98, 0.02, 0.95, 0.045) is True


def test_outcomes_for():
    hg, ag = pd.Series([2, 1, 0, 1]), pd.Series([0, 1, 3, 2])
    assert list(outcomes_for("1X2", hg, ag)) == [0, 1, 2, 2]
    assert list(outcomes_for("OU25", hg, ag)) == [0, 0, 1, 1]
    assert list(outcomes_for("BTTS", hg, ag)) == [0, 1, 0, 1]


def _features(rng, n):
    return pd.DataFrame(rng.normal(0, 1, (n, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)


def test_learns_signal_missing_from_layer1():
    rng = np.random.default_rng(42)
    n = 5000
    feats = _features(rng, n)
    p = 1 / (1 + np.exp(-(1.5 * feats["h_pts5"].to_numpy())))
    y = pd.Series((rng.random(n) < p).astype(int))
    l1 = pd.DataFrame({"KGVAR": np.full(n, 0.5)})
    model, metrics = evaluate_and_train(feats, l1, y, "BTTS")
    assert model is not None
    assert metrics["l2_ll"] < metrics["l1_ll"]


def test_rejected_when_no_signal():
    rng = np.random.default_rng(7)
    n = 5000
    feats = _features(rng, n)
    true_p = rng.uniform(0.2, 0.8, n)
    y = pd.Series((rng.random(n) < true_p).astype(int))
    l1 = pd.DataFrame({"KGVAR": true_p})
    model, metrics = evaluate_and_train(feats, l1, y, "BTTS")
    assert model is None


def test_predict_1x2_rows_sum_to_one(tmp_path):
    rng = np.random.default_rng(3)
    n = 3000
    feats = _features(rng, n)
    s = feats["elo_diff"].to_numpy()
    probs = np.column_stack([np.exp(s), np.ones(n), np.exp(-s)])
    probs /= probs.sum(axis=1, keepdims=True)
    y = pd.Series([rng.choice(3, p=row) for row in probs])
    l1 = pd.DataFrame({"MS1": np.full(n, 0.45), "MSX": np.full(n, 0.27), "MS2": np.full(n, 0.28)})
    model, _ = evaluate_and_train(feats, l1, y, "1X2")
    assert model is not None
    out = model.predict(feats.head(10), l1.head(10))
    assert out.shape == (10, 3)
    assert np.allclose(out.sum(axis=1), 1.0)
    save_layer2({"1X2": model, "OU25": None}, tmp_path / "l2.pkl")
    loaded = load_layer2(tmp_path / "l2.pkl")
    assert loaded["OU25"] is None and np.allclose(loaded["1X2"].predict(feats.head(3), l1.head(3)), out[:3])
