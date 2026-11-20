import pytest

from app.model.market_layer import apply_market, blend_1x2, blend_binary, demargin, fit_goals
from app.model.markets import market_probs, score_matrix


def test_demargin_removes_bookmaker_share():
    q = demargin({"MS1": 2.0, "MSX": 3.5, "MS2": 4.0}, ["MS1", "MSX", "MS2"])
    assert sum(q) == pytest.approx(1.0) and q[0] > q[2]
    assert demargin({"MS1": 2.0, "MSX": 3.5}, ["MS1", "MSX", "MS2"]) is None
    assert demargin({"MS1": 2.0, "MSX": 1.0, "MS2": 4.0}, ["MS1", "MSX", "MS2"]) is None


def test_blends_lean_on_market():
    p = blend_1x2([0.70, 0.20, 0.10], [0.45, 0.28, 0.27])
    assert sum(p) == pytest.approx(1.0)
    assert abs(p[0] - 0.45) < 0.05  # piyasa ağırlıklı
    o = blend_binary(0.60, 0.50)
    assert 0.50 < o < 0.55


def test_fit_goals_reproduces_targets():
    truth = market_probs(score_matrix(1.8, 0.9, -0.05))
    lam, mu = fit_goals(truth["MS1"], truth["MS2"], truth["UST25"], -0.05, 1.2, 1.2)
    assert lam == pytest.approx(1.8, abs=0.02) and mu == pytest.approx(0.9, abs=0.02)


def test_apply_market_is_consistent():
    model = market_probs(score_matrix(1.5, 1.1, -0.05))
    odds = {"MS1": 1.80, "MSX": 3.80, "MS2": 4.50, "UST25": 1.70, "ALT25": 2.15}
    probs, lam, mu, matrix = apply_market(model, 1.5, 1.1, -0.05, odds)
    assert probs["MS1"] + probs["MSX"] + probs["MS2"] == pytest.approx(1.0)
    assert probs["CS12"] == pytest.approx(probs["MS1"] + probs["MS2"])
    assert probs["UST25"] + probs["ALT25"] == pytest.approx(1.0)
    assert probs["KGVAR"] + probs["KGYOK"] == pytest.approx(1.0)
    assert probs["MS1"] > model["MS1"] and lam > mu  # piyasa ev sahibini daha güçlü görüyor
    assert matrix.sum() == pytest.approx(1.0)
    assert apply_market(model, 1.5, 1.1, -0.05, {"UST25": 1.7, "ALT25": 2.1}) is None
