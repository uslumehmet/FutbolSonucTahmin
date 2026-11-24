"""ESPN'den önümüzdeki maçlar: saatler, oranlar ve milli maçlar (API-Football'a ihtiyaç duymadan).

API-Football yanıt vermediğinde (hesap askıda, anahtar yok ya da istek atılmayan bir sunucu) site boş kalmasın diye
ESPN'in herkese açık maç listesinden önümüzdeki 3 günün maçları, saatleri ve maç öncesi oranları alınır.

- Kulüp ligleri: maç listesinde DraftKings oranları (Maç Sonucu, tek bir Alt/Üst çizgisi). Maça 48 saatten az kalmış
  maçlarda maç ayrıntısı da istenir; büyük maçlarda orada Bet365 oranı (Çifte Şans dahil) bulunur.
- Milli maçlar (Uluslar Ligi, elemeler, turnuvalar): maçlar, oranlar ve dünün/bugünün sonuçları.
- Karşılıklı Gol oranı ve sakatlık/ceza bilgisi ESPN'de yok; bunlar yalnızca API-Football'dan gelir.

API-Football'dan gelen maçın saati, API yanıt verirken değiştirilmez; API verisi her zaman esastır.
ESPN resmi, belgelenmiş bir API değildir; adres ya da biçim değişirse bu kaynak hata verir, site çalışmaya devam eder.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx

from app.config import CLUB_LEAGUES, NATIONAL_COMPETITIONS, TZ, current_season_start
from app.data.espn import ESPN_DIR, SLUGS, _season_teams, day_url, parse_odds, resolve_names, summary_url
from app.data.http import fetch_text, is_fresh
from app.data.teams import resolve_team, seed_aliases
from app.model.markets import MARKETS
from app.models import Competition, Match, OddsQuote, Team

log = logging.getLogger(__name__)

NATIONAL_SLUGS = {"WC": "fifa.world", "WCQ_EU": "fifa.worldq.uefa", "EURO": "uefa.euro", "EUROQ": "uefa.euroq",
                  "UNL": "uefa.nations"}
NEUTRAL_CODES = {"WC", "EURO"}  # turnuva maçları tarafsız sahada (ingest_api.NEUTRAL_COMPETITIONS ile aynı)
UPCOMING_DAYS = 3
BOARD_MAX_AGE = timedelta(hours=6)  # oranlar gün içinde değişir; maç listesi en fazla bu kadar saklanır
SUMMARY_MAX_AGE = timedelta(hours=12)
SUMMARY_HOURS = 48  # maç ayrıntısı (Bet365) yalnızca bu kadar saat içinde başlayacak maçlar için istenir
SLIM_ABOVE = 50_000  # maç ayrıntısı dosyası büyükse önbelleğe yalnızca oran kısmı yazılır
ESPN_SOURCE = "espn"


@dataclass
class UpcomingReport:
    requests: int = 0
    summary_requests: int = 0
    matches: int = 0
    created: int = 0
    odds: int = 0
    results: int = 0
    unresolved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped: bool = False


def parse_events(text: str) -> list[dict]:
    """Maç listesindeki her maç: espn_id, season, kickoff_utc, home, away, state, status, fthg, ftag, odds."""
    data = json.loads(text)
    out = []
    for e in data.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in sides or "away" not in sides or not e.get("date"):
            continue
        st = (e.get("status") or {}).get("type") or {}
        row = {"espn_id": str(e["id"]) if e.get("id") else None, "season": (e.get("season") or {}).get("year"),
               "kickoff_utc": datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
               "home": sides["home"]["team"]["displayName"], "away": sides["away"]["team"]["displayName"],
               "state": st.get("state"), "status": _status(st), "fthg": None, "ftag": None,
               "odds": odds_sets(comp.get("odds"))}
        if row["status"] == "finished":
            try:
                row["fthg"], row["ftag"] = int(sides["home"]["score"]), int(sides["away"]["score"])
            except (KeyError, TypeError, ValueError):
                row["status"] = "live"
        out.append(row)
    return out


def _status(st: dict) -> str:
    name = str(st.get("name") or "").upper()
    if "POSTPONED" in name:
        return "postponed"
    if "CANCEL" in name:
        return "cancelled"
    if "ABANDON" in name:
        return "abandoned"
    if st.get("completed") and st.get("state") == "post":
        return "finished"
    return "scheduled" if st.get("state") == "pre" else "live"


def odds_sets(odds_list) -> dict[str, dict[str, float]]:
    """ESPN oran listesi -> {"DK": {...}, "B365": {...}} (ondalık). DraftKings'te kapanış, yoksa açılış oranı."""
    parsed = parse_odds({"odds": [o for o in odds_list or [] if isinstance(o, dict)]})
    out: dict[str, dict[str, float]] = {}
    dk = parsed.get("odds_dk") or {}
    row = dk.get("close") or dk.get("open")
    if row:
        out["DK"] = row
    if parsed.get("odds_b365"):
        out["B365"] = parsed["odds_b365"]
    return {book: {m: v for m, v in odds.items() if m in MARKETS} for book, odds in out.items()}


def store_espn_odds(session, match_id: int, sets: dict[str, dict[str, float]], now: datetime) -> bool:
    """ESPN oranlarını yazar; aynı bahisçinin eski ESPN oranları silinir (her güncellemede satır birikmesin)."""
    wrote = False
    for book, odds in sets.items():
        if not odds:
            continue
        session.query(OddsQuote).filter_by(match_id=match_id, source=ESPN_SOURCE, bookmaker=book).delete(
            synchronize_session=False)
        for market, value in odds.items():
            session.add(OddsQuote(match_id=match_id, bookmaker=book, market=market, odds=value, fetched_at=now,
                                  source=ESPN_SOURCE))
        wrote = True
    return wrote


class _Getter:
    def __init__(self, fetch, rep: UpcomingReport, max_requests: int, max_summaries: int):
        self.fetch, self.rep, self.max_requests, self.max_summaries = fetch, rep, max_requests, max_summaries

    def board(self, url, dest) -> str | None:
        if not is_fresh(dest, BOARD_MAX_AGE):
            if self.rep.requests >= self.max_requests:
                self.rep.stopped = True
                return None
            self.rep.requests += 1
        return self.fetch(url, dest, BOARD_MAX_AGE)

    def summary(self, url, dest) -> str | None:
        if not is_fresh(dest, SUMMARY_MAX_AGE):
            if self.rep.summary_requests >= self.max_summaries:
                return None
            self.rep.summary_requests += 1
        return self.fetch(url, dest, SUMMARY_MAX_AGE)


def _events(get: _Getter, rep: UpcomingReport, slug: str, code: str, name: str, days: list[date]) -> list[dict]:
    seen: dict[str, dict] = {}
    for d in days:
        try:
            text = get.board(day_url(slug, d), ESPN_DIR / "yakin" / code / f"{d:%Y%m%d}.json")
            if text is None:
                break
            for e in parse_events(text):
                seen[e["espn_id"] or f"{e['home']}-{e['away']}-{e['kickoff_utc']}"] = e
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            rep.errors.append(f"{name} {d}: {exc}")
    return list(seen.values())


def _apply(m: Match, e: dict, api_is_live: bool) -> None:
    """Plan maçının saatini ve durumunu ESPN'e göre düzeltir (API'den gelen maça API yanıt verirken dokunmaz)."""
    if m.api_fixture_id is None or not api_is_live:
        m.kickoff_utc = e["kickoff_utc"]
        if e["status"] in ("scheduled", "postponed", "cancelled", "abandoned"):
            m.status = e["status"]
    if e["espn_id"] and (m.stats or {}).get("espn_id") != e["espn_id"]:
        m.stats = {**(m.stats or {}), "espn_id": e["espn_id"]}


def _fixture(session, rep: UpcomingReport, m: Match | None, comp: Competition, season: int, home: int, away: int,
             e: dict, now: datetime, api_is_live: bool) -> Match | None:
    """Gelecek maçı kurar ya da günceller, oranını yazar. Dönen maç, maç ayrıntısı (Bet365) için adaydır."""
    if m is not None and m.status == "finished":
        return None
    if m is None:
        m = Match(competition_id=comp.id, season=season, home_team_id=home, away_team_id=away,
                  kickoff_utc=e["kickoff_utc"], status=e["status"], neutral=comp.code in NEUTRAL_CODES,
                  stats={"espn_id": e["espn_id"]} if e["espn_id"] else {})
        session.add(m)
        session.flush()
        rep.created += 1
    else:
        _apply(m, e, api_is_live)
    rep.matches += 1
    if m.status == "scheduled" and m.kickoff_utc > now and store_espn_odds(session, m.id, e["odds"], now):
        rep.odds += 1
    return m if m.status == "scheduled" else None


def _club_league(session, get: _Getter, rep: UpcomingReport, lg, days: list[date], now: datetime,
                 api_is_live: bool, soon: list) -> None:
    slug = SLUGS[lg.code]
    comp = session.query(Competition).filter_by(code=lg.code).one()
    events = [e for e in _events(get, rep, slug, lg.code, lg.name, days) if e["status"] != "live"
              and e["status"] != "finished"]  # biten kulüp maçları espn.ingest_espn'de işlenir
    by_season: dict[int, list[dict]] = {}
    for e in events:
        by_season.setdefault(e["season"] or current_season_start(now.astimezone(TZ).date()), []).append(e)
    for season, evs in by_season.items():
        if not _season_teams(session, comp, season):
            rep.errors.append(f"{lg.name} {season}: sezonun takımları henüz yok, ESPN maçları atlandı")
            continue
        names = list(dict.fromkeys([e["home"] for e in evs] + [e["away"] for e in evs]))
        ids, unresolved = resolve_names(session, comp, season, names, lg.country)
        rep.unresolved += [f"{lg.name} {season}: {n}" for n in unresolved]
        existing: dict[tuple[int, int], list[Match]] = {}
        for m in session.query(Match).filter_by(competition_id=comp.id, season=season):
            existing.setdefault((m.home_team_id, m.away_team_id), []).append(m)
        for e in evs:
            if e["home"] not in ids or e["away"] not in ids:
                continue
            key = (ids[e["home"]], ids[e["away"]])
            cands = existing.get(key, [])
            m = next((x for x in cands if x.status != "finished"), cands[0] if cands else None)
            m = _fixture(session, rep, m, comp, season, key[0], key[1], e, now, api_is_live)
            if m is not None:
                existing.setdefault(key, []).append(m)
                soon.append((m, slug, lg.code))
    session.commit()


def _national(session, get: _Getter, rep: UpcomingReport, lg, days: list[date], now: datetime,
              api_is_live: bool, pool: list[Team], soon: list) -> None:
    slug = NATIONAL_SLUGS[lg.code]
    comp = session.query(Competition).filter_by(code=lg.code).one()
    for e in _events(get, rep, slug, lg.code, lg.name, days):
        if e["status"] == "live":
            continue
        home = resolve_team(session, "espn", e["home"], pool)
        away = resolve_team(session, "espn", e["away"], pool)
        missing = [n for n, t in ((e["home"], home), (e["away"], away)) if t is None]
        if missing:
            rep.unresolved += [f"{lg.name}: {n}" for n in missing]
            continue
        tol = timedelta(hours=36)
        m = (session.query(Match).filter(Match.competition_id == comp.id, Match.home_team_id == home.id,
                                         Match.away_team_id == away.id,
                                         Match.kickoff_utc >= e["kickoff_utc"] - tol,
                                         Match.kickoff_utc <= e["kickoff_utc"] + tol).first())
        season = e["season"] or e["kickoff_utc"].year
        if e["status"] == "finished":
            if m is None:
                m = Match(competition_id=comp.id, season=season, home_team_id=home.id, away_team_id=away.id,
                          kickoff_utc=e["kickoff_utc"], neutral=lg.code in NEUTRAL_CODES,
                          stats={"espn_id": e["espn_id"]} if e["espn_id"] else {})
                session.add(m)
            elif m.status == "finished":
                continue
            m.status, m.home_goals, m.away_goals = "finished", e["fthg"], e["ftag"]
            rep.results += 1
            continue
        m = _fixture(session, rep, m, comp, season, home.id, away.id, e, now, api_is_live)
        if m is not None:
            soon.append((m, slug, lg.code))
    session.commit()


def _bet365_from_summaries(session, get: _Getter, rep: UpcomingReport, soon: list, now: datetime) -> None:
    """Yakın maçların ayrıntısından Bet365 oranı (Çifte Şans dahil); yalnızca büyük maçlarda bulunur."""
    limit = now + timedelta(hours=SUMMARY_HOURS)
    for m, slug, code in sorted(soon, key=lambda x: x[0].kickoff_utc):
        espn_id = (m.stats or {}).get("espn_id")
        if not espn_id or not (now < m.kickoff_utc <= limit):
            continue
        dest = ESPN_DIR / "yakin_ozet" / code / f"{espn_id}.json"
        try:
            text = get.summary(summary_url(slug, espn_id), dest)
            if text is None:
                break
            data = json.loads(text)
            if len(text) > SLIM_ABOVE:  # önbelleğe yalnızca oranlar (dosya ~150 KB yerine birkaç KB)
                dest.write_text(json.dumps({"odds": data.get("odds")}, ensure_ascii=False), encoding="utf-8")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            rep.errors.append(f"maç ayrıntısı {espn_id}: {exc}")
            continue
        sets = odds_sets(data.get("odds"))
        if sets.get("B365") and store_espn_odds(session, m.id, {"B365": sets["B365"]}, now):
            rep.odds += 1
    session.commit()


def ingest_espn_upcoming(session, now: datetime, fetch=fetch_text, api_is_live: bool = False,
                         days: int = UPCOMING_DAYS, summaries: bool = True, max_requests: int = 60,
                         max_summaries: int = 40, codes: list[str] | None = None) -> UpcomingReport:
    """Önümüzdeki `days` günün kulüp ve milli maçlarını, saatlerini ve oranlarını ESPN'den yazar.

    api_is_live: API-Football yakın zamanda yanıt verdiyse API'den gelen maçların saati değiştirilmez ve maç
    ayrıntısı (Bet365) istenmez; oranlar zaten API'den gelir.
    """
    rep = UpcomingReport()
    seed_aliases(session)
    get = _Getter(fetch, rep, max_requests, max_summaries)
    today = now.astimezone(TZ).date()
    club_days = [today + timedelta(days=i) for i in range(days)]
    soon: list = []
    for lg in CLUB_LEAGUES:
        if lg.code in SLUGS and (codes is None or lg.code in codes) and not rep.stopped:
            _club_league(session, get, rep, lg, club_days, now, api_is_live, soon)
    pool = session.query(Team).filter_by(kind="national", is_temp=False).all()
    for lg in NATIONAL_COMPETITIONS:
        if lg.code in NATIONAL_SLUGS and (codes is None or lg.code in codes) and not rep.stopped:
            # dünün maçları da: milli maç sonuçları buradan gelir (martj42 verisi günler sonra güncellenir)
            _national(session, get, rep, lg, [today - timedelta(days=1)] + club_days, now, api_is_live, pool, soon)
    if summaries and not api_is_live:
        _bet365_from_summaries(session, get, rep, soon, now)
    return rep
