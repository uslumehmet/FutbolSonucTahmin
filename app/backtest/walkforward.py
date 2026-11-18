"""Yürüyen pencere (walk-forward) Katman 1 tahminleri ve Katman 2 eğitimi."""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd

from app.config import CLUB_LEAGUES, set_value
from app.model.dixon_coles import fit_dixon_coles
from app.model.elo import CLUB_HOME_ADV, CLUB_K, elo_history
from app.model.features import FEATURE_COLUMNS, build_features
from app.model.layer2 import GROUPS, evaluate_and_train, outcomes_for, save_layer2, save_layer2_db
from app.model.markets import MARKETS, market_probs, score_matrix, top_scores
from app.model.store import DC_SEASONS, HALF_LIFE_DAYS, LAYER2_PATH, competition_frame
from app.models import Competition, Prediction

log = logging.getLogger(__name__)
MIN_TRAIN_ROWS = 300


def walkforward_club(session, comp_code: str, start_year: int, end_year: int, refit_days: int = 7) -> int:
    comp = session.query(Competition).filter_by(code=comp_code).one()
    df = competition_frame(session, [comp.id])
    target = df[(df["season"] >= start_year) & (df["season"] <= end_year) & df["home_goals"].notna()]
    if target.empty:
        return 0
    done = {r[0] for r in session.query(Prediction.match_id).filter(
        Prediction.kind == "walkforward", Prediction.match_id.in_([int(x) for x in target["id"]]))}
    t = target["kickoff_utc"].min().floor("D")
    end = target["kickoff_utc"].max()
    prev, n = None, 0
    while t <= end:
        t_next = t + timedelta(days=refit_days)
        window = target[(target["kickoff_utc"] >= t) & (target["kickoff_utc"] < t_next) & ~target["id"].isin(done)]
        if len(window):
            season = int(window["season"].max())
            train = df[(df["season"] >= season - (DC_SEASONS - 1)) & df["home_goals"].notna()]
            ref = t.to_pydatetime()
            try:
                model = fit_dixon_coles(train, ref, half_life_days=HALF_LIFE_DAYS, init=prev)
            except ValueError:
                t = t_next
                continue
            prev = model
            for row in window.itertuples():
                lam, mu = model.expected_goals(int(row.home_id), int(row.away_id), bool(row.neutral))
                mat = score_matrix(lam, mu, model.rho)
                session.add(Prediction(match_id=int(row.id), kind="walkforward", created_at=ref, lam_home=lam,
                                       lam_away=mu, probs=market_probs(mat), layers={g: 1 for g in GROUPS},
                                       flags=[], missing_snapshot=[], top_scores=[list(x) for x in top_scores(mat, 5)]))
            n += len(window)
        t = t_next
    session.commit()
    return n


def _latest_probs(session, match_ids: list[int]) -> dict[int, dict]:
    """Maç başına: varsa walkforward, yoksa başlamadan önceki son canlı tahmin olasılıkları."""
    out: dict[int, dict] = {}
    rows = (session.query(Prediction.match_id, Prediction.kind, Prediction.probs, Prediction.created_at)
            .filter(Prediction.match_id.in_(match_ids)).order_by(Prediction.created_at).all())
    for mid, kind, probs, _ in rows:
        if not probs:
            continue
        if kind == "walkforward" or mid not in out or out[mid][0] != "walkforward":
            out[mid] = (kind, probs)
    return {k: v[1] for k, v in out.items()}


def training_frame(session, comp_codes: list[str]) -> pd.DataFrame:
    parts = []
    for code in comp_codes:
        comp = session.query(Competition).filter_by(code=code).one()
        df = competition_frame(session, [comp.id])
        if df.empty:
            continue
        hist, _ = elo_history(df, home_adv=CLUB_HOME_ADV, k_fn=lambda r: CLUB_K)
        feats = build_features(hist)
        done = df[df["home_goals"].notna()]
        probs = _latest_probs(session, [int(x) for x in done["id"]])
        if not probs:
            continue
        pdf = pd.DataFrame.from_dict(probs, orient="index")[MARKETS]
        merged = done.set_index("id").join(feats, how="inner").join(pdf, how="inner")
        merged["competition"] = code
        parts.append(merged.reset_index().rename(columns={"index": "match_id", "id": "match_id"}))
    if not parts:
        return pd.DataFrame(columns=["match_id", "kickoff_utc", "season", *FEATURE_COLUMNS, *MARKETS,
                                     "home_goals", "away_goals"])
    out = pd.concat(parts, ignore_index=True).sort_values("kickoff_utc", kind="stable").reset_index(drop=True)
    return out


def train_group_models(df: pd.DataFrame) -> tuple[dict, dict]:
    models, metrics = {}, {}
    for group in GROUPS:
        if len(df) < MIN_TRAIN_ROWS:
            models[group] = None
            metrics[group] = {"kept": False, "reason": "veri yetersiz", "n": int(len(df))}
            continue
        feats = df[FEATURE_COLUMNS].copy()
        feats.attrs["max_kickoff"] = df["kickoff_utc"].max()
        y = outcomes_for(group, df["home_goals"], df["away_goals"])
        model, m = evaluate_and_train(feats, df[MARKETS].reset_index(drop=True), y, group)
        models[group], metrics[group] = model, m
    return models, metrics


def train_layer2_all(session, before=None, path=LAYER2_PATH) -> dict[str, dict]:
    df = training_frame(session, [lg.code for lg in CLUB_LEAGUES])
    if before is not None:
        df = df[df["kickoff_utc"] < pd.Timestamp(before)]
    models, metrics = train_group_models(df)
    save_layer2(models, path)
    save_layer2_db(session, models)
    set_value(session, "layer2_active", {g: models[g] is not None for g in GROUPS})
    set_value(session, "layer2_metrics", metrics)
    return metrics
