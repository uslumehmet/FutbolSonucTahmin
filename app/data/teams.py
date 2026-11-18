"""Takım adlarını kaynaklar arasında eşleştirme."""
from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from pathlib import Path

from sqlalchemy import or_

from app.models import Match, Team, TeamAlias

ALIASES_FILE = Path(__file__).with_name("aliases.csv")
_STOP = {"fc", "cf", "sk", "fk", "as", "ac", "afc", "sc", "ssc", "cd", "ud", "rc", "rcd", "jk"}
_TR = str.maketrans({"İ": "i", "I": "i", "ı": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                     "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u"})
FUZZY_THRESHOLD = 0.85


def normalize(name: str) -> str:
    s = name.translate(_TR).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    words = [w for w in s.split() if w not in _STOP]
    return " ".join(words)


def _alias(session, source: str, norm: str) -> TeamAlias | None:
    return session.query(TeamAlias).filter_by(source=source, alias_norm=norm).one_or_none()


def add_alias(session, team_id: int, source: str, raw_name: str) -> None:
    norm = normalize(raw_name)
    existing = _alias(session, source, norm)
    if existing is None:
        session.add(TeamAlias(team_id=team_id, source=source, alias_norm=norm))
    else:
        existing.team_id = team_id
    session.commit()


def get_or_create_team(session, source: str, raw_name: str, kind: str, country: str | None = None) -> Team:
    norm = normalize(raw_name)
    alias = _alias(session, source, norm)
    if alias is not None:
        return session.get(Team, alias.team_id)
    team = Team(name=raw_name.strip(), kind=kind, country=country)
    session.add(team)
    session.flush()
    session.add(TeamAlias(team_id=team.id, source=source, alias_norm=norm))
    session.commit()
    return team


def _tokens(norm: str) -> set[str]:
    return set(norm.split())


def resolve_team(session, source: str, raw_name: str, candidates: list[Team]) -> Team | None:
    """Alias → tam eşleşme → kelime kapsama (tekil) → bulanık eşleşme (net en iyi)."""
    norm = normalize(raw_name)
    alias = _alias(session, source, norm)
    if alias is not None:
        team = session.get(Team, alias.team_id)
        if team is not None:
            return team
    cands = [(t, normalize(t.name)) for t in candidates]
    exact = [t for t, n in cands if n == norm]
    if len(exact) == 1:
        add_alias(session, exact[0].id, source, raw_name)
        return exact[0]
    mine = _tokens(norm)
    contained = []
    for t, n in cands:
        other = _tokens(n)
        small = mine if len(mine) <= len(other) else other
        big = other if small is mine else mine
        if small and small <= big and any(len(w) >= 4 for w in small):
            contained.append(t)
    if len(contained) == 1:
        add_alias(session, contained[0].id, source, raw_name)
        return contained[0]
    if len(contained) > 1:
        return None
    scored = sorted(((difflib.SequenceMatcher(None, norm, n).ratio(), t) for t, n in cands),
                    key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= FUZZY_THRESHOLD:
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05:
            add_alias(session, scored[0][1].id, source, raw_name)
            return scored[0][1]
    return None


def seed_aliases(session) -> int:
    if not ALIASES_FILE.exists():
        return 0
    added = 0
    with ALIASES_FILE.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            canon = normalize(row["canonical"])
            target = (
                session.query(TeamAlias)
                .filter(TeamAlias.alias_norm == canon, TeamAlias.source.in_(("of", "intl")))
                .first()
            )
            if target is None:
                continue
            norm = normalize(row["alias"])
            existing = _alias(session, row["source"], norm)
            if existing is None:
                session.add(TeamAlias(team_id=target.team_id, source=row["source"], alias_norm=norm))
                added += 1
            elif existing.team_id != target.team_id:
                current = session.get(Team, existing.team_id)
                if current is not None and current.is_temp:
                    # Daha önce eşleşmeyip geçici kaydedilen takımı doğru takıma bağla
                    api_id = current.api_id
                    merge_team(session, current.id, target.team_id)
                    canonical = session.get(Team, target.team_id)
                    if api_id and canonical.api_id is None:
                        canonical.api_id = api_id
                    added += 1
    session.commit()
    return added


def _refresh_unmatched(session, team_id: int) -> None:
    matches = session.query(Match).filter(
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id), Match.unmatched.is_(True)
    )
    for m in matches:
        home, away = session.get(Team, m.home_team_id), session.get(Team, m.away_team_id)
        if not home.is_temp and not away.is_temp:
            m.unmatched = False


def merge_team(session, temp_team_id: int, canonical_team_id: int) -> None:
    temp = session.get(Team, temp_team_id)
    if temp is None or temp_team_id == canonical_team_id:
        return
    session.query(Match).filter(Match.home_team_id == temp_team_id).update({"home_team_id": canonical_team_id})
    session.query(Match).filter(Match.away_team_id == temp_team_id).update({"away_team_id": canonical_team_id})
    for al in session.query(TeamAlias).filter_by(team_id=temp_team_id).all():
        al.team_id = canonical_team_id
    norm = normalize(temp.name)
    if _alias(session, "api", norm) is None:
        session.add(TeamAlias(team_id=canonical_team_id, source="api", alias_norm=norm))
    from app.models import MissingPlayer

    session.query(MissingPlayer).filter_by(team_id=temp_team_id).update({"team_id": canonical_team_id})
    session.flush()
    session.delete(temp)
    session.flush()
    _refresh_unmatched(session, canonical_team_id)
    _drop_planned_duplicates(session, canonical_team_id)
    session.commit()


def _drop_planned_duplicates(session, team_id: int) -> None:
    """openfootball fikstüründen gelmiş plan maçı ile aynı maçın API kaydı birleşince plan kaydını siler.

    Elle eklenen eksik oyuncular API kaydına taşınır. Kupona girmiş bir kayda dokunulmaz.
    """
    from app.models import CouponPick, MissingPlayer, OddsQuote, Prediction, SinglePick

    api_matches = session.query(Match).filter(
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id), Match.api_fixture_id.isnot(None)).all()
    for m in api_matches:
        dups = session.query(Match).filter(
            Match.id != m.id, Match.competition_id == m.competition_id, Match.season == m.season,
            Match.home_team_id == m.home_team_id, Match.away_team_id == m.away_team_id,
            Match.api_fixture_id.is_(None), Match.status == "scheduled").all()
        for d in dups:
            if session.query(CouponPick).filter_by(match_id=d.id).count()                     or session.query(SinglePick).filter_by(match_id=d.id).count():
                continue
            session.query(MissingPlayer).filter_by(match_id=d.id, source="manual").update({"match_id": m.id})
            session.query(MissingPlayer).filter_by(match_id=d.id).delete()
            session.query(OddsQuote).filter_by(match_id=d.id).delete()
            session.query(Prediction).filter_by(match_id=d.id).delete()
            session.delete(d)
    session.flush()


def confirm_new_team(session, team_id: int) -> None:
    team = session.get(Team, team_id)
    if team is None:
        return
    team.is_temp = False
    session.flush()
    _refresh_unmatched(session, team_id)
    session.commit()
