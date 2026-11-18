"""Kaynaklardan veritabanına veri yükleme."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
from sqlalchemy import or_

from app.config import CLUB_LEAGUES, RAW_DIR, current_season_start
from app.data.http import fetch_text
from app.data.openfootball import (
    DEFAULT_TIME, JSON_SLUG, LEAGUE_TZ, json_url, parse_of_json, parse_of_json_fixtures, parse_of_txt,
    parse_of_txt_fixtures, txt_url,
)
from app.data.teams import add_alias, normalize, resolve_team, seed_aliases
from app.models import Competition, Match, Team

log = logging.getLogger(__name__)


def _py(ts) -> datetime:
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def find_match(session, home_id: int, away_id: int, kickoff_utc: datetime,
               tolerance: timedelta = timedelta(hours=36)) -> Match | None:
    return (
        session.query(Match)
        .filter(
            Match.home_team_id == home_id,
            Match.away_team_id == away_id,
            Match.kickoff_utc >= kickoff_utc - tolerance,
            Match.kickoff_utc <= kickoff_utc + tolerance,
        )
        .first()
    )


def competition_teams(session, comp: Competition) -> list[Team]:
    ids = {r[0] for r in session.query(Match.home_team_id).filter_by(competition_id=comp.id)}
    ids |= {r[0] for r in session.query(Match.away_team_id).filter_by(competition_id=comp.id)}
    if not ids:
        return []
    return session.query(Team).filter(Team.id.in_(ids)).all()


def _resolve_season_names(session, comp: Competition, names: list[str], country: str) -> dict[str, int]:
    """Bir sezon dosyasındaki adları takımlara eşler; aynı sezonda iki ad asla aynı takıma düşmez.

    Önce alias/tam eşleşmeler sabitlenir, kalanlar yalnızca bu sezonda henüz sahiplenilmemiş
    takımlar arasında kapsama/bulanık eşleşmeyle aranır, bulunamayanlar yeni takım olur.
    """
    known = competition_teams(session, comp)
    out: dict[str, int] = {}
    pending = []
    for name in names:
        team = resolve_team(session, "of", name, [t for t in known if normalize(t.name) == normalize(name)])
        if team is not None and team.id not in out.values():
            out[name] = team.id
        else:
            pending.append(name)
    for name in pending:
        free = [t for t in known if t.id not in out.values()]
        team = resolve_team(session, "of", name, free)
        if team is None or team.id in out.values():
            team = Team(name=name.strip(), kind="club", country=country)
            session.add(team)
            session.flush()
            add_alias(session, team.id, "of", name)
            known.append(team)
        out[name] = team.id
    return out


def _upsert_results(session, comp: Competition, season: int, df: pd.DataFrame, country: str,
                    upcoming: pd.DataFrame | None = None, now: datetime | None = None) -> int:
    """Oynanmış maçları yükler; `upcoming` verilirse `now`'dan sonraki fikstürleri de "scheduled" olarak ekler.

    Lig maçlarında bir sezonda her ev sahibi–deplasman ikilisi bir kez oynar; anahtar budur.
    Döndürülen sayı yalnızca sonuçları (oynanmış maçları) sayar.
    """
    upcoming = upcoming if upcoming is not None else pd.DataFrame(columns=["kickoff_utc", "home", "away"])
    if df.empty and upcoming.empty:
        return 0
    existing = {
        (m.home_team_id, m.away_team_id): m
        for m in session.query(Match).filter_by(competition_id=comp.id, season=season)
    }
    names = list(dict.fromkeys(list(df["home"]) + list(df["away"]) + list(upcoming["home"]) + list(upcoming["away"])))
    ids = _resolve_season_names(session, comp, names, country)
    n = 0
    for row in df.to_dict("records"):
        h, a = ids[row["home"]], ids[row["away"]]
        m = existing.get((h, a))
        if m is None:
            m = Match(competition_id=comp.id, season=season, home_team_id=h, away_team_id=a,
                      kickoff_utc=_py(row["kickoff_utc"]), stats={})
            session.add(m)
            existing[(h, a)] = m
        elif m.api_fixture_id is not None and m.status == "finished":
            n += 1
            continue  # API sonucu esas alınır
        m.kickoff_utc = _py(row["kickoff_utc"])
        m.status = "finished"
        m.home_goals, m.away_goals = int(row["fthg"]), int(row["ftag"])
        n += 1
    for row in upcoming.to_dict("records"):
        ko = _py(row["kickoff_utc"])
        if now is None or ko <= now:
            continue  # geçmişte kalmış ama skoru gelmemiş maç: sonucu bekle, fikstür olarak ekleme
        h, a = ids[row["home"]], ids[row["away"]]
        m = existing.get((h, a))
        if m is None:
            m = Match(competition_id=comp.id, season=season, home_team_id=h, away_team_id=a,
                      kickoff_utc=ko, status="scheduled", stats={})
            session.add(m)
            existing[(h, a)] = m
        elif m.status == "scheduled" and m.api_fixture_id is None:
            m.kickoff_utc = ko  # fikstür değiştiyse güncelle; API'den gelen maçlara dokunma
    session.commit()
    return n


def ingest_openfootball(session, start_years: list[int], today: date, fetch=fetch_text, leagues=None,
                        now: datetime | None = None) -> int:
    """openfootball'dan kulüp sonuçlarını yükler. Bulunmayan sezonlar (404) atlanır.

    Bu sezonun dosyasındaki henüz oynanmamış maçlar da (`now`'dan sonrakiler) fikstür olarak eklenir;
    ücretsiz API yalnızca dün/bugün/yarını gördüğü için ileriki günlerin maçları buradan gelir.
    """
    leagues = leagues or CLUB_LEAGUES
    current = current_season_start(today)
    if now is None:
        from app.config import TZ
        now = datetime.combine(today, datetime.min.time(), tzinfo=TZ).astimezone(timezone.utc)
    total = 0
    for lg in leagues:
        comp = session.query(Competition).filter_by(code=lg.code).one()
        tz = LEAGUE_TZ[lg.code]
        for year in start_years:
            max_age = None if year < current else timedelta(days=3)
            if lg.code in JSON_SLUG:
                url, dest = json_url(lg.code, year), RAW_DIR / "of" / f"{lg.code}_{year}.json"
            else:
                url, dest = txt_url(year), RAW_DIR / "of" / f"{lg.code}_{year}.txt"
            try:
                text = fetch(url, dest, max_age)
            except httpx.HTTPError as exc:
                log.info("openfootball yok/indirilemedi %s %s: %s", lg.code, year, exc)
                continue
            upcoming = None
            if lg.code in JSON_SLUG:
                df = parse_of_json(text, tz)
                if year == current:
                    upcoming = parse_of_json_fixtures(text, tz)
            else:
                df = parse_of_txt(text, tz, year, DEFAULT_TIME.get(lg.code, "19:00"))
                if year == current:
                    upcoming = parse_of_txt_fixtures(text, tz, year, DEFAULT_TIME.get(lg.code, "19:00"))
            total += _upsert_results(session, comp, year, df, lg.country, upcoming=upcoming, now=now)
    seed_aliases(session)
    return total


def matches_of_team(session, team_id: int):
    return session.query(Match).filter(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))


def ingest_international(session, fetch=fetch_text) -> int:
    """Milli maç geçmişini INTL_HIST yarışmasına yükler (idempotent)."""
    from app.data.international import INTL_URL, parse_international
    from app.data.teams import get_or_create_team

    text = fetch(INTL_URL, RAW_DIR / "intl" / "results.csv", timedelta(days=3))
    df = parse_international(text)
    comp = session.query(Competition).filter_by(code="INTL_HIST").one()
    existing = {
        (m.home_team_id, m.away_team_id, m.kickoff_utc.date()): m
        for m in session.query(Match).filter_by(competition_id=comp.id)
    }
    cache: dict[str, int] = {}

    def tid(name: str) -> int:
        if name not in cache:
            cache[name] = get_or_create_team(session, "intl", name, "national", name).id
        return cache[name]

    n = 0
    for row in df.to_dict("records"):
        ko = _py(row["kickoff_utc"])
        h, a = tid(row["home"]), tid(row["away"])
        m = existing.get((h, a, ko.date()))
        if m is None:
            m = Match(competition_id=comp.id, season=ko.year, kickoff_utc=ko, home_team_id=h, away_team_id=a)
            session.add(m)
            existing[(h, a, ko.date())] = m
        m.neutral = bool(row["neutral"])
        m.status = "finished"
        m.home_goals, m.away_goals = int(row["home_score"]), int(row["away_score"])
        m.stats = {"tier": row["tier"], "tournament": row["tournament"]}
        n += 1
    session.commit()
    return n
