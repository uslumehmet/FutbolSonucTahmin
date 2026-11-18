from datetime import UTC, datetime

from app.model.explain import explain
from app.model.features import RecentMatch
from app.model.injuries import MissingImpact


def _rm(res):
    gf, ga = {"G": (2, 0), "B": (1, 1), "M": (0, 1)}[res]
    return RecentMatch(datetime(2026, 1, 1, tzinfo=UTC), "X", "E", gf, ga, res, "Süper Lig")


def test_explain_strings():
    lines = explain(
        "Galatasaray", "Kasımpaşa", 2.05, 0.8,
        home_form=[_rm(r) for r in "GGGBG"], away_form=[_rm(r) for r in "MMBMM"],
        h2h=[_rm(r) for r in "GGB"],
        missing=[("Kasımpaşa", "Golcü", MissingImpact(0.08, 0.0)), ("Galatasaray", "Kaleci", MissingImpact(0.0, 0.06))],
        elo_diff=180.0,
    )
    assert lines == [
        "Gol beklentisi: Galatasaray 2.05 – 0.80 Kasımpaşa",
        "Galatasaray son 5 maçta 13 puan topladı (iyi form)",
        "Kasımpaşa son 5 maçta 1 puan topladı (kötü form)",
        "Kasımpaşa: Golcü eksik (hücuma etkisi yüksek)",
        "Galatasaray: Kaleci eksik (savunmaya etkisi)",
        "Aralarındaki son 3 maç: 2 galibiyet, 1 beraberlik, 0 mağlubiyet (Galatasaray açısından)",
        "Galatasaray güç olarak belirgin önde",
    ]


def test_explain_minimal():
    assert explain("A", "B", 1.2, 1.1, [], [], [], [], None) == ["Gol beklentisi: A 1.20 – 1.10 B"]
