"""Form özellikleri (sızıntısız) ve arayüz için son maç listeleri."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import and_, or_

from app.models import Competition, Match, Team

FEATURE_COLUMNS = ["elo_diff", "h_pts5", "a_pts5", "h_pts10", "a_pts10", "h_gf5", "h_ga5", "a_gf5", "a_ga5",
                   "h_home_pts5", "a_away_pts5", "h_rest", "a_rest"]
MAX_REST = 14


def _pts(gf: int, ga: int) -> int:
    return 3 if gf > ga else 1 if gf == ga else 0


def _mean(seq, n=None):
    vals = list(seq)[-n:] if n else list(seq)
    return float(np.mean(vals)) if vals else np.nan


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    data = df.sort_values("kickoff_utc", kind="stable")
    hist: dict[int, deque] = {}
    home_hist: dict[int, deque] = {}
    away_hist: dict[int, deque] = {}
    last_ko: dict[int, pd.Timestamp] = {}
    out = {}
    has_elo = "elo_home" in data and "elo_away" in data
    for row in data.itertuples(index=False):
        h, a = int(row.home_id), int(row.away_id)
        ko = pd.Timestamp(row.kickoff_utc)
        hh = hist.setdefault(h, deque(maxlen=10))
        ah = hist.setdefault(a, deque(maxlen=10))
        hhome = home_hist.setdefault(h, deque(maxlen=5))
        aaway = away_hist.setdefault(a, deque(maxlen=5))

        def rest(team):
            if team not in last_ko:
                return np.nan
            return float(min(MAX_REST, (ko - last_ko[team]).total_seconds() / 86400))

        out[row.id] = {
            "elo_diff": float(row.elo_home - row.elo_away) if has_elo else np.nan,
            "h_pts5": _mean((p for p, _, _ in hh), 5) if hh else np.nan,
            "a_pts5": _mean((p for p, _, _ in ah), 5) if ah else np.nan,
            "h_pts10": _mean(p for p, _, _ in hh) if hh else np.nan,
            "a_pts10": _mean(p for p, _, _ in ah) if ah else np.nan,
            "h_gf5": _mean((g for _, g, _ in hh), 5) if hh else np.nan,
            "h_ga5": _mean((c for _, _, c in hh), 5) if hh else np.nan,
            "a_gf5": _mean((g for _, g, _ in ah), 5) if ah else np.nan,
            "a_ga5": _mean((c for _, _, c in ah), 5) if ah else np.nan,
            "h_home_pts5": _mean(hhome) if hhome else np.nan,
            "a_away_pts5": _mean(aaway) if aaway else np.nan,
            "h_rest": rest(h),
            "a_rest": rest(a),
        }
        if pd.notna(row.home_goals) and pd.notna(row.away_goals):
            hg, ag = int(row.home_goals), int(row.away_goals)
            hh.append((_pts(hg, ag), hg, ag))
            ah.append((_pts(ag, hg), ag, hg))
            hhome.append(_pts(hg, ag))
            aaway.append(_pts(ag, hg))
            last_ko[h] = last_ko[a] = ko
    return pd.DataFrame.from_dict(out, orient="index")[FEATURE_COLUMNS]


@dataclass
class RecentMatch:
    kickoff_utc: datetime
    opponent: str
    venue: str  # "E" ev, "D" deplasman, "T" tarafsız
    gf: int
    ga: int
    result: str  # "G", "B", "M"
    competition: str


def _to_recent(m: Match, team_id: int, names: dict[int, str], comps: dict[int, str]) -> RecentMatch:
    home = m.home_team_id == team_id
    gf, ga = (m.home_goals, m.away_goals) if home else (m.away_goals, m.home_goals)
    venue = "T" if m.neutral else ("E" if home else "D")
    res = "G" if gf > ga else "B" if gf == ga else "M"
    opp = m.away_team_id if home else m.home_team_id
    return RecentMatch(m.kickoff_utc, names.get(opp, "?"), venue, gf, ga, res, comps.get(m.competition_id, ""))


def _names(session, matches) -> tuple[dict[int, str], dict[int, str]]:
    ids = {t for m in matches for t in (m.home_team_id, m.away_team_id)}
    names = {t.id: t.name for t in session.query(Team).filter(Team.id.in_(ids))} if ids else {}
    comps = {c.id: c.name for c in session.query(Competition)}
    return names, comps


def recent_matches(session, team_id: int, before: datetime, n: int = 10, venue: str | None = None) -> list[RecentMatch]:
    q = session.query(Match).filter(Match.status == "finished", Match.kickoff_utc < before,
                                    Match.unmatched.isnot(True))
    if venue == "E":
        q = q.filter(Match.home_team_id == team_id, Match.neutral.is_(False))
    elif venue == "D":
        q = q.filter(Match.away_team_id == team_id, Match.neutral.is_(False))
    else:
        q = q.filter(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
    ms = q.order_by(Match.kickoff_utc.desc()).limit(n).all()
    names, comps = _names(session, ms)
    return [_to_recent(m, team_id, names, comps) for m in ms]


def head_to_head(session, a_id: int, b_id: int, before: datetime, n: int = 6) -> list[RecentMatch]:
    ms = (session.query(Match)
          .filter(Match.status == "finished", Match.kickoff_utc < before,
                  or_(and_(Match.home_team_id == a_id, Match.away_team_id == b_id),
                      and_(Match.home_team_id == b_id, Match.away_team_id == a_id)))
          .order_by(Match.kickoff_utc.desc()).limit(n).all())
    names, comps = _names(session, ms)
    return [_to_recent(m, a_id, names, comps) for m in ms]


# ---------------------------------------------------------------- şut ve kadro özellikleri (ESPN maç ayrıntısı)

SHOT_COLUMNS = ["h_sot5", "h_sota5", "a_sot5", "a_sota5", "h_sh5", "h_sha5", "a_sh5", "a_sha5",
                "h_sot10", "h_sota10", "a_sot10", "a_sota10"]
LINEUP_COLUMNS = ["h_xi_core", "a_xi_core", "h_xi_changes", "a_xi_changes"]
CORE_LOOKBACK = 10  # kadro gücü: son kaç maçın ilk 11'ine bakılır
MIN_LINEUPS = 5


def _stat_pair(stats, key):
    v = (stats or {}).get(key) if isinstance(stats, dict) else None
    if not v or len(v) != 2 or v[0] is None or v[1] is None:
        return None
    return float(v[0]), float(v[1])


def extra_features(df: pd.DataFrame) -> pd.DataFrame:
    """Şut ve kadro özellikleri (sızıntısız: her maç sadece kendinden önceki maçlarla).

    df: competition_frame çıktısı ("stats" sütunu ile). Şut verisi olmayan maçlar ortalamaya girmez.
    Kadro: takımın son CORE_LOOKBACK maçında en çok ilk 11'e çıkan 11 oyuncu "düzenli kadro"dur;
    h_xi_core = bu maçın ilk 11'inde düzenli kadrodan kaç oyuncu var / 11. Maç başlamadan bilinemez
    (ilk 11 ~1 saat önce açıklanır); sadece maç öncesi yenileme yapılırsa kullanılabilir.
    """
    data = df.sort_values("kickoff_utc", kind="stable")
    shots: dict[int, deque] = {}
    xis: dict[int, deque] = {}
    out = {}
    for row in data.itertuples(index=False):
        h, a = int(row.home_id), int(row.away_id)
        hs = shots.setdefault(h, deque(maxlen=10))
        as_ = shots.setdefault(a, deque(maxlen=10))
        rec = {}
        for p, q in (("h", hs), ("a", as_)):
            rec[f"{p}_sot5"] = _mean((x[0] for x in q), 5) if q else np.nan
            rec[f"{p}_sota5"] = _mean((x[1] for x in q), 5) if q else np.nan
            rec[f"{p}_sh5"] = _mean((x[2] for x in q), 5) if q else np.nan
            rec[f"{p}_sha5"] = _mean((x[3] for x in q), 5) if q else np.nan
            rec[f"{p}_sot10"] = _mean(x[0] for x in q) if q else np.nan
            rec[f"{p}_sota10"] = _mean(x[1] for x in q) if q else np.nan
        stats = row.stats if isinstance(row.stats, dict) else {}
        for p, team, side in (("h", h, "home"), ("a", a, "away")):
            hist = xis.setdefault(team, deque(maxlen=CORE_LOOKBACK))
            xi = {pl[0] for pl in stats.get(f"xi_{side}") or []}
            if xi and len(hist) >= MIN_LINEUPS:
                counts: dict[str, int] = {}
                for past in hist:
                    for pid in past:
                        counts[pid] = counts.get(pid, 0) + 1
                core = set(sorted(counts, key=lambda k: -counts[k])[:11])
                rec[f"{p}_xi_core"] = len(xi & core) / 11
                rec[f"{p}_xi_changes"] = float(len(xi - hist[-1]))
            else:
                rec[f"{p}_xi_core"] = np.nan
                rec[f"{p}_xi_changes"] = np.nan
            if xi:
                hist.append(xi)
        out[row.id] = rec
        if pd.notna(row.home_goals):
            sot, sh = _stat_pair(stats, "sot"), _stat_pair(stats, "shots")
            if sot and sh:
                hs.append((sot[0], sot[1], sh[0], sh[1]))
                as_.append((sot[1], sot[0], sh[1], sh[0]))
    return pd.DataFrame.from_dict(out, orient="index")[SHOT_COLUMNS + LINEUP_COLUMNS]
