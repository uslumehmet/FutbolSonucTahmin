"""Takım adlarını kaynaklar arasında eşleştirme."""
from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from datetime import timedelta
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
    canonical = session.get(Team, canonical_team_id)
    if temp.api_id and canonical is not None and canonical.api_id is None:
        canonical.api_id = temp.api_id  # API bir sonraki güncellemede de bu takımı bulsun (eşleştirme geri alındıysa)
    session.flush()
    session.delete(temp)
    session.flush()
    _refresh_unmatched(session, canonical_team_id)
    _drop_planned_duplicates(session, canonical_team_id)
    _join_finished_duplicates(session, canonical_team_id)
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


DUPLICATE_WINDOW = timedelta(hours=36)  # upsert_api_fixture ile aynı: maç saati kaynaklar arasında kayabilir


def _join_finished_duplicates(session, team_id: int) -> None:
    """Takım eşleşmemişken aynı maç iki kez bitmiş olabilir: ESPN sonucu asıl takımın kaydına, API kaydı geçici
    takıma yazılır (21 Kasım 2026: Lille–Lyon, Lorient–Rennes). Eşleştirince ikisi tek kayıtta birleşir.

    Asıl takımın kaydı kalır (tahminleri ve kupon seçimleri ona bağlı); API kimliği, oranlar, eksikler ve
    varsa seçimler ona taşınır. Skorlar farklıysa ya da aday birden fazlaysa hangisinin doğru olduğu bilinmez,
    dokunulmaz.
    """
    from app.models import CouponPick, MissingPlayer, OddsQuote, Prediction, SinglePick

    api_matches = session.query(Match).filter(
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        Match.api_fixture_id.isnot(None), Match.status == "finished").all()
    for m in api_matches:
        twins = session.query(Match).filter(
            Match.id != m.id, Match.competition_id == m.competition_id, Match.season == m.season,
            Match.home_team_id == m.home_team_id, Match.away_team_id == m.away_team_id,
            Match.api_fixture_id.is_(None), Match.status == "finished",
            Match.home_goals == m.home_goals, Match.away_goals == m.away_goals,
            Match.kickoff_utc.between(m.kickoff_utc - DUPLICATE_WINDOW, m.kickoff_utc + DUPLICATE_WINDOW)).all()
        if len(twins) != 1:
            continue
        keep = twins[0]
        # Eşleşmemiş kopyanın tahminleri boş yer tutucudur ("eşleşmedi"); taşınırsa son tahmin diye o görünür.
        session.query(Prediction).filter(Prediction.match_id == m.id, Prediction.lam_home.is_(None)).delete(
            synchronize_session=False)
        for table in (OddsQuote, Prediction, MissingPlayer, CouponPick, SinglePick):
            session.query(table).filter_by(match_id=m.id).update({"match_id": keep.id}, synchronize_session=False)
        keep.stats = {**(m.stats or {}), **(keep.stats or {})}
        fixture_id = m.api_fixture_id
        session.delete(m)
        session.flush()  # api_fixture_id benzersiz: önce eski kayıt gitmeli
        keep.api_fixture_id = fixture_id
    session.flush()


def unmatch_team(session, team_id: int) -> int | None:
    """Takımın API eşleştirmesini kaldırır; takım Ayarlar'daki "Eşleşmeyen takımlar"a döner.

    API dışı adı (openfootball, ESPN, milli maç) olan takımda API kimliği (API adları, api_id ve API'den gelen
    maçlar) yeni bir geçici takıma ayrılır; diğer maçlar yerinde kalır. Bir sonraki API güncellemesi takımı
    api_id ile bu geçici kayıtta bulur, kendiliğinden yeniden eşleşmez. Sadece API adı olan takım (API'nin açıp
    "Ligi yeni çıktı" ile onaylanan) geçici yapılır. API bağlantısı yoksa bir şey yapmaz, None döner.
    Döndürdüğü: Eşleşmeyen takımlar listesinde görünecek takımın id'si.
    """
    from app.models import MissingPlayer

    team = session.get(Team, team_id)
    if team is None:
        return None
    aliases = session.query(TeamAlias).filter_by(team_id=team_id).all()
    api_aliases = [a for a in aliases if a.source == "api"]
    if not api_aliases and team.api_id is None:
        return None
    of_team = or_(Match.home_team_id == team_id, Match.away_team_id == team_id)
    if all(a.source == "api" for a in aliases):
        team.is_temp = True
        session.query(Match).filter(of_team).update({"unmatched": True}, synchronize_session=False)
        session.commit()
        return team_id
    temp = Team(name=team.name, kind=team.kind, country=team.country, is_temp=True, api_id=team.api_id)
    team.api_id = None
    session.add(temp)
    session.flush()
    for a in api_aliases:
        a.team_id = temp.id
    moved = session.query(Match).filter(of_team, Match.api_fixture_id.isnot(None)).all()
    for m in moved:
        if m.home_team_id == team_id:
            m.home_team_id = temp.id
        if m.away_team_id == team_id:
            m.away_team_id = temp.id
        m.unmatched = True
    if moved:
        session.query(MissingPlayer).filter(MissingPlayer.team_id == team_id,
                                            MissingPlayer.match_id.in_([m.id for m in moved])
                                            ).update({"team_id": temp.id}, synchronize_session=False)
    session.commit()
    return temp.id


def promoted_teams(session, today) -> list[tuple]:
    """Her kulüp liginde bu sezon oynayıp geçen sezon o ligde oynamayan takımlar (lige yeni çıkanlar).

    Geçici takımlar hariç (onlar "Eşleşmeyen takımlar"da). Geçen sezonun verisi olmayan lig atlanır.
    """
    from app.config import CLUB_LEAGUES, current_season_start
    from app.models import Competition

    season = current_season_start(today)

    def team_ids(comp_id: int, s: int) -> set[int]:
        rows = session.query(Match.home_team_id, Match.away_team_id).filter_by(competition_id=comp_id, season=s)
        return {tid for row in rows for tid in row}

    out = []
    for lg in CLUB_LEAGUES:
        comp = session.query(Competition).filter_by(code=lg.code).one_or_none()
        if comp is None:
            continue
        before = team_ids(comp.id, season - 1)
        new = team_ids(comp.id, season) - before
        if not before or not new:
            continue
        teams = session.query(Team).filter(Team.id.in_(new), Team.is_temp.is_(False)).order_by(Team.name).all()
        out += [(comp, t) for t in teams]
    return out


def confirm_new_team(session, team_id: int) -> None:
    team = session.get(Team, team_id)
    if team is None:
        return
    team.is_temp = False
    session.flush()
    _refresh_unmatched(session, team_id)
    session.commit()
