"""Sayfalar için görünüm verisi (veritabanından şablona)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from urllib.parse import quote_plus

from app.betting.selection import (
    Candidate, candidates_for, eligible, implied_probs, latest_odds, safe_pick, value_pick,
)
from app.config import TZ, get_settings
from app.model.explain import explain
from app.model.features import head_to_head, recent_matches
from app.model.injuries import MissingImpact
from app.model.markets import MARKETS, market_label
from app.model.predict import latest_prediction, repredict
from app.models import Competition, Match, MissingPlayer, Team

DAY_WINDOWS = {"bugun": (0, 1), "yarin": (1, 2), "3gun": (0, 3), "7gun": (0, 7)}
DAY_LABELS = {"bugun": "Bugün", "yarin": "Yarın", "3gun": "3 gün"}  # kupon sayfası (oran gereken aralık)
MATCH_DAY_LABELS = {**DAY_LABELS, "7gun": "7 gün"}  # maçlar sayfası
SCHEDULE_FLAG = "oran 1 gün önce gelir"
SHOWN = ("scheduled", "finished", "postponed", "cancelled", "abandoned")


def day_window(now: datetime, gun: str) -> tuple[datetime, datetime]:
    a, b = DAY_WINDOWS.get(gun, DAY_WINDOWS["bugun"])
    today = now.astimezone(TZ).date()
    start = datetime.combine(today + timedelta(days=a), time(0, 0), tzinfo=TZ)
    end = datetime.combine(today + timedelta(days=b), time(0, 0), tzinfo=TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


@dataclass
class Picks:
    safe: Candidate | None
    value: Candidate | None
    book: str | None
    cands: list[Candidate]


def picks_for(session, match: Match, probs: dict, low_data: bool = False) -> Picks:
    s = get_settings(session)
    lo = latest_odds(session, match.id)
    if not probs or lo is None:
        return Picks(None, None, lo[0] if lo else None, [])
    cands = candidates_for(match.id, probs, lo[1], s.odds_adjust_pct, low_data)
    safe = safe_pick(eligible(cands, "safe", s), s.safe_min_odds)
    value = value_pick(eligible(cands, "value", s), s.value_min_prob, s.value_min_edge)
    return Picks(safe, value, lo[0], cands)


def match_rows(session, now: datetime, gun: str, lig: str | None) -> list[dict]:
    start, end = day_window(now, gun)
    q = (session.query(Match).join(Competition, Match.competition_id == Competition.id)
         .filter(Match.kickoff_utc >= start, Match.kickoff_utc < end, Match.status.in_(SHOWN),
                 Competition.kind != "hidden"))
    if lig:
        q = q.filter(Competition.code == lig)
    groups: dict[int, dict] = {}
    for m in q.order_by(Match.kickoff_utc).all():
        pred = latest_prediction(session, m.id)
        probs = pred.probs if pred else {}
        flags = list(pred.flags) if pred else []
        if m.unmatched and "eşleşmedi" not in flags:
            flags.append("eşleşmedi")
        pk = picks_for(session, m, probs, "veri az" in flags)
        if m.status == "scheduled" and m.api_fixture_id is None and not pk.cands:
            flags.append(SCHEDULE_FLAG)  # sezon fikstüründen gelen maç: oran ve kesinleşmiş saat henüz yok
        elif probs and not pk.cands and m.status == "scheduled":
            flags.append("oran yok")
        missing = session.query(MissingPlayer).filter_by(match_id=m.id, active=True).count()
        g = groups.setdefault(m.competition_id, {"competition": m.competition, "rows": []})
        g["rows"].append({"m": m, "probs": probs, "flags": flags, "picks": pk, "missing": missing})
    order = {c: i for i, c in enumerate(["T1", "E0", "SP1", "I1", "D1", "F1", "WC", "WCQ_EU", "EURO", "EUROQ", "UNL"])}
    return sorted(groups.values(), key=lambda g: order.get(g["competition"].code, 99))


def next_match_day(session, now: datetime, lig: str | None = None) -> dict | None:
    """Şu andan sonraki ilk maç gününü (Türkiye saatiyle) ve o gündeki maç sayısını döndürür."""
    q = (session.query(Match.kickoff_utc).join(Competition, Match.competition_id == Competition.id)
         .filter(Match.status == "scheduled", Match.kickoff_utc > now, Competition.kind != "hidden"))
    if lig:
        q = q.filter(Competition.code == lig)
    first = q.order_by(Match.kickoff_utc).first()
    if first is None:
        return None
    day = first[0].astimezone(TZ).date()
    start = datetime.combine(day, time(0, 0), tzinfo=TZ).astimezone(UTC)
    count = q.filter(Match.kickoff_utc >= start, Match.kickoff_utc < start + timedelta(days=1)).count()
    return {"kickoff": first[0], "count": count, "days_ahead": (day - now.astimezone(TZ).date()).days}


def h2h_rows(session, a_id: int, b_id: int, before: datetime, n: int = 6) -> list[dict]:
    """Aralarındaki maçlar, gerçek ev sahibi – deplasman ve skor ile."""
    from sqlalchemy import and_, or_
    ms = (session.query(Match)
          .filter(Match.status == "finished", Match.kickoff_utc < before,
                  or_(and_(Match.home_team_id == a_id, Match.away_team_id == b_id),
                      and_(Match.home_team_id == b_id, Match.away_team_id == a_id)))
          .order_by(Match.kickoff_utc.desc()).limit(n).all())
    rows = []
    for x in ms:
        winner = None
        if x.home_goals > x.away_goals:
            winner = x.home_team.name
        elif x.away_goals > x.home_goals:
            winner = x.away_team.name
        rows.append({"date": x.kickoff_utc, "home": x.home_team.name, "away": x.away_team.name,
                     "hg": x.home_goals, "ag": x.away_goals, "winner": winner, "competition": x.competition.name})
    return rows


def transfermarkt_url(team_name: str) -> str:
    return "https://www.transfermarkt.com.tr/schnellsuche/ergebnis/schnellsuche?query=" + quote_plus(team_name)


def match_detail(session, match_id: int, now: datetime) -> dict | None:
    m = session.get(Match, match_id)
    if m is None:
        return None
    pred = latest_prediction(session, m.id)
    if pred is None and not m.unmatched and m.status == "scheduled":
        repredict(session, m.id, now)
        pred = latest_prediction(session, m.id)
    probs = pred.probs if pred else {}
    pk = picks_for(session, m, probs, bool(pred and "veri az" in (pred.flags or [])))
    odds = {c.market: c.odds for c in pk.cands}
    raw = latest_odds(session, m.id)
    implied = implied_probs(raw[1]) if raw else {}
    from app.models import OddsQuote
    last_q = (session.query(OddsQuote).filter_by(match_id=m.id)
              .order_by(OddsQuote.fetched_at.desc()).first())
    odds_time = last_q.fetched_at if last_q else None
    markets = []
    for key in MARKETS:
        if key not in probs:
            continue
        o = odds.get(key)
        markets.append({"key": key, "label": market_label(key, m.home_team.name, m.away_team.name), "prob": probs[key], "odds": o,
                        "implied": implied.get(key), "value": probs[key] * o - 1 if o else None})
    before = min(now, m.kickoff_utc)
    home, away = m.home_team, m.away_team
    form = {
        "home_all": recent_matches(session, home.id, before, 10),
        "home_venue": recent_matches(session, home.id, before, 10, venue="E"),
        "away_all": recent_matches(session, away.id, before, 10),
        "away_venue": recent_matches(session, away.id, before, 10, venue="D"),
    }
    h2h = head_to_head(session, home.id, away.id, before, 6)
    snap = {s["id"]: s for s in (pred.missing_snapshot if pred else [])}
    missing = []
    for mp in session.query(MissingPlayer).filter_by(match_id=m.id).order_by(MissingPlayer.team_id).all():
        sn = snap.get(mp.id, {})
        imp = MissingImpact(sn.get("attack", mp.attack_effect or 0.0), sn.get("concede", mp.concede_effect or 0.0))
        team = session.get(Team, mp.team_id)
        missing.append({"mp": mp, "team": team.name if team else "?", "impact": imp})
    lines = []
    if pred and pred.lam_home is not None:
        lines = explain(home.name, away.name, pred.lam_home, pred.lam_away, form["home_all"], form["away_all"], h2h,
                        [(x["team"], x["mp"].player_name, x["impact"]) for x in missing if x["mp"].active], None)
    by_key = {x["key"]: x for x in markets}
    from app.model.markets import MARKET_SECTIONS
    sections = [(title, [by_key[k] for k in keys if k in by_key]) for title, keys in MARKET_SECTIONS]
    team_titles = {"Ev sahibi golleri": f"{m.home_team.name} golleri (ev sahibi)",
                   "Deplasman golleri": f"{m.away_team.name} golleri (deplasman)"}
    sections = [(team_titles.get(t, t), rows) for t, rows in sections if rows]
    return {"m": m, "pred": pred, "probs": probs, "markets": markets, "picks": pk, "form": form, "h2h": h2h,
            "sections": sections, "h2h_rows": h2h_rows(session, home.id, away.id, before, 6),
            "odds_time": odds_time,
            "missing": missing, "lines": lines, "tm_home": transfermarkt_url(home.name),
            "tm_away": transfermarkt_url(away.name)}
