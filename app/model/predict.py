"""Maç tahmini akışı: Katman 1 (+ eksik düzeltmesi) → pazarlar → Katman 2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from app.config import TZ
from app.model.injuries import MissingImpact, adjust_lambdas, player_impact
from app.model.layer2 import GROUPS
from app.model.markets import market_probs, probs_from_1x2, score_matrix, top_scores
from app.model.store import ModelStore
from app.models import Match, MissingPlayer, PlayerSeasonStat, Prediction, Team

MIN_MATCHES = 5


@dataclass
class MatchPrediction:
    match_id: int
    lam_home: float | None
    lam_away: float | None
    probs: dict[str, float]
    layers: dict[str, int]
    flags: list[str]
    top_scores: list[tuple[int, int, float]] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)


def missing_impacts(session, match: Match) -> tuple[list[MissingImpact], list[MissingImpact], list[dict], bool]:
    """(ev eksikleri, deplasman eksikleri, anlık görüntü, istatistik eksik mi)."""
    home, away, snap, lacking = [], [], [], False
    rows = session.query(MissingPlayer).filter_by(match_id=match.id, active=True).all()
    for mp in rows:
        if mp.source == "manual":
            imp = MissingImpact(mp.attack_effect or 0.0, mp.concede_effect or 0.0)
        else:
            stat = session.get(PlayerSeasonStat, (mp.api_player_id, match.season)) if mp.api_player_id else None
            if stat is None:
                lacking = True
                imp = MissingImpact(0.0, 0.0)
            else:
                imp = player_impact(stat.position, stat.minutes, stat.goals, stat.assists, stat.team_games,
                                    stat.team_goals)
        (home if mp.team_id == match.home_team_id else away).append(imp)
        snap.append({"id": mp.id, "team_id": mp.team_id, "player": mp.player_name, "source": mp.source,
                     "reason": mp.reason, "attack": round(imp.attack_effect, 4), "concede": round(imp.concede_effect, 4)})
    return home, away, snap, lacking


def predict_match(session, store: ModelStore, match: Match, now: datetime) -> MatchPrediction:
    if match.unmatched:
        return MatchPrediction(match.id, None, None, {}, {}, ["eşleşmedi"])
    flags: list[str] = []
    comp = match.competition
    as_of = now.astimezone(TZ).date()
    h, a = match.home_team_id, match.away_team_id
    if comp.kind == "club":
        dc = store.club_model(comp.code, as_of)
        lam, mu = dc.expected_goals(h, a, match.neutral)
        rho = dc.rho
        if min(dc.n_matches.get(h, 0), dc.n_matches.get(a, 0)) < MIN_MATCHES:
            flags.append("veri az")
    else:
        elo, gm, counts = store.national(as_of)
        lam, mu = gm.expected_goals(elo.rating(h), elo.rating(a), match.neutral)
        rho = 0.0
        if min(counts.get(h, 0), counts.get(a, 0)) < MIN_MATCHES:
            flags.append("veri az")
    home_m, away_m, snap, lacking = missing_impacts(session, match)
    if lacking:
        flags.append("eksik verisi yok")
    lam, mu = adjust_lambdas(lam, mu, home_m, away_m)
    matrix = score_matrix(lam, mu, rho)
    probs = market_probs(matrix)
    layers = {g: 1 for g in GROUPS}
    if comp.kind == "club":
        l2 = store.layer2()
        if any(l2.values()):
            feats = store.club_features(comp.code, as_of)
            if match.id in feats.index:
                frow = feats.loc[[match.id]]
                l1 = pd.DataFrame([probs])
                for group, model in l2.items():
                    if model is None:
                        continue
                    out = model.predict(frow, l1)
                    if group == "1X2":
                        p1, px, p2 = (float(x) for x in out[0])
                        probs = probs_from_1x2(p1, px, p2, probs)
                    elif group == "OU25":
                        probs["UST25"], probs["ALT25"] = float(out[0]), 1 - float(out[0])
                    elif group == "BTTS":
                        probs["KGVAR"], probs["KGYOK"] = float(out[0]), 1 - float(out[0])
                    layers[group] = 2
    return MatchPrediction(match.id, lam, mu, probs, layers, flags, top_scores(matrix, 5), snap)


def _store(session, p: MatchPrediction, now: datetime, kind: str = "live") -> Prediction:
    row = Prediction(match_id=p.match_id, kind=kind, created_at=now, lam_home=p.lam_home, lam_away=p.lam_away,
                     probs=p.probs, layers=p.layers, flags=p.flags, missing_snapshot=p.missing,
                     top_scores=[list(t) for t in p.top_scores])
    session.add(row)
    return row


def predict_upcoming(session, store: ModelStore, now: datetime, days: int = 3) -> list[MatchPrediction]:
    matches = (session.query(Match)
               .filter(Match.status == "scheduled", Match.kickoff_utc >= now,
                       Match.kickoff_utc <= now + timedelta(days=days))
               .order_by(Match.kickoff_utc).all())
    out = []
    for m in matches:
        p = predict_match(session, store, m, now)
        _store(session, p, now)
        out.append(p)
    session.commit()
    return out


def repredict(session, match_id: int, now: datetime) -> MatchPrediction:
    m = session.get(Match, match_id)
    p = predict_match(session, ModelStore(session), m, now)
    _store(session, p, now)
    session.commit()
    return p


def latest_prediction(session, match_id: int, before: datetime | None = None) -> Prediction | None:
    q = session.query(Prediction).filter_by(match_id=match_id, kind="live")
    if before is not None:
        q = q.filter(Prediction.created_at < before)
    return q.order_by(Prediction.created_at.desc(), Prediction.id.desc()).first()
