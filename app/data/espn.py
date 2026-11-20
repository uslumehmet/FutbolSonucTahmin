"""İkinci sonuç kaynağı: ESPN'in herkese açık maç sonuçları (anahtar gerekmez).

API-Football'un ücretsiz planı geçmiş günleri ve 2025 sonrası sezonları vermiyor, openfootball da geç
güncelleniyor. ESPN'den sadece biten maçların skoru alınır; oran, sakatlık ve tahmin işleri API-Football'da kalır.
ESPN resmi, belgelenmiş bir API değildir; adres değişirse bu kaynak hata verir ama site çalışmaya devam eder.

Her lig sezonunun maç günleri ESPN yanıtındaki takvimden okunur, yalnızca maç olan günler istenir.
İndirilen her gün `data/raw/espn/` altında saklanır; tamamlanan günler veritabanında işaretlenir ve bir daha işlenmez.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
import pandas as pd

from app.config import CLUB_LEAGUES, RAW_DIR, current_season_start, get_value, set_value
from app.data.http import fetch_text, is_fresh
from app.data.teams import add_alias, resolve_team, seed_aliases
from app.models import Competition, Match, Team

log = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SLUGS = {"T1": "tur.1", "E0": "eng.1", "SP1": "esp.1", "D1": "ger.1", "I1": "ita.1", "F1": "fra.1"}
ESPN_DIR = RAW_DIR / "espn"
DONE_KEY = "espn_done"  # işlenmiş ve bir daha değişmeyecek günler: "T1:20261018"
SETTLED_AFTER = timedelta(days=2)  # bu kadar eski günler kesinleşmiş sayılır (dosya bir daha indirilmez)


@dataclass
class EspnReport:
    requests: int = 0
    days: int = 0
    results: int = 0
    unresolved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped: bool = False  # istek sınırına gelindi; kalan günler bir sonraki çalışmada


def day_url(slug: str, day: date) -> str:
    return f"{BASE}/{slug}/scoreboard?dates={day:%Y%m%d}"


def _calendar_days(league: dict) -> list[date]:
    out = []
    for entry in league.get("calendar") or []:
        if isinstance(entry, str):
            out.append(date.fromisoformat(entry[:10]))
        elif isinstance(entry, dict):  # bazı liglerde takvim bölümler hâlinde gelir
            for sub in entry.get("entries") or []:
                if sub.get("startDate"):
                    out.append(date.fromisoformat(sub["startDate"][:10]))
    return sorted(set(out))


def parse_scoreboard(text: str) -> tuple[int | None, list[date], list[dict]]:
    """(sezon başlangıç yılı, maç günleri, maçlar). Maç: espn_id, season, kickoff_utc, home, away, completed, fthg, ftag.

    Bir sezonun takvimi bazen sonraki sezonun ilk günlerini de içerir (2021-22 takviminde Ağustos 2022);
    bu yüzden her maçın kendi sezon bilgisi de okunur."""
    data = json.loads(text)
    league = (data.get("leagues") or [{}])[0]
    season = (league.get("season") or {}).get("year")
    events = []
    for e in data.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in sides or "away" not in sides:
            continue
        status = (e.get("status") or {}).get("type") or {}
        completed = bool(status.get("completed")) and status.get("state") == "post"
        row = {"espn_id": str(e["id"]) if e.get("id") else None, "season": (e.get("season") or {}).get("year"),
               "kickoff_utc": datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
               "home": sides["home"]["team"]["displayName"], "away": sides["away"]["team"]["displayName"],
               "completed": completed, "fthg": None, "ftag": None}
        if completed:
            try:
                row["fthg"], row["ftag"] = int(sides["home"]["score"]), int(sides["away"]["score"])
            except (KeyError, TypeError, ValueError):
                row["completed"] = False
        events.append(row)
    return season, _calendar_days(league), events


def _season_teams(session, comp: Competition, season: int) -> list[Team]:
    ids = {i for m in session.query(Match.home_team_id, Match.away_team_id)
           .filter_by(competition_id=comp.id, season=season) for i in m}
    return session.query(Team).filter(Team.id.in_(ids)).all() if ids else []


def resolve_names(session, comp: Competition, season: int, names: list[str],
                  country: str) -> tuple[dict[str, int], list[str]]:
    """ESPN adlarını takımlara eşler. Sezonun takımları veritabanında varsa sadece onların arasında arar ve
    bulamadığı adı eşlemez (yanlış eşleşme ya da çift takım olmasın); böyle adlar için aliases.csv'ye satır eklenir.
    Sezon veritabanında hiç yoksa (ör. Süper Lig 2021-22) ligin bütün takımları arasında arar, yoksa yeni takım açar."""
    season_teams = _season_teams(session, comp, season)
    pool = list(season_teams) or list(_competition_teams(session, comp))
    out: dict[str, int] = {}
    unresolved = []
    for name in names:
        free = [t for t in pool if t.id not in out.values()]
        team = resolve_team(session, "espn", name, free)
        if team is not None and team.id in out.values():
            team = None
        if team is None and not season_teams:
            team = Team(name=name.strip(), kind="club", country=country)
            session.add(team)
            session.flush()
            add_alias(session, team.id, "espn", name)
            pool.append(team)
        if team is None:
            unresolved.append(name)
        else:
            out[name] = team.id
    return out, unresolved


def _competition_teams(session, comp: Competition) -> list[Team]:
    from app.data.ingest import competition_teams
    return competition_teams(session, comp)


def _upsert(session, comp: Competition, season: int, rows: list[dict], ids: dict[str, int]) -> int:
    """Biten maçları yazar. API-Football'dan gelmiş bitmiş maça dokunmaz; plan maçını (fikstür) sonuçlandırır."""
    existing = {(m.home_team_id, m.away_team_id): m
                for m in session.query(Match).filter_by(competition_id=comp.id, season=season)}
    n = 0
    for r in rows:
        if r["home"] not in ids or r["away"] not in ids:
            continue
        key = (ids[r["home"]], ids[r["away"]])
        m = existing.get(key)
        if m is None:
            m = Match(competition_id=comp.id, season=season, home_team_id=key[0], away_team_id=key[1],
                      kickoff_utc=r["kickoff_utc"], stats={})
            session.add(m)
            existing[key] = m
        if r.get("espn_id") and (m.stats or {}).get("espn_id") != r["espn_id"]:
            m.stats = {**(m.stats or {}), "espn_id": r["espn_id"]}  # maç ayrıntısı (şut, ilk 11) için
        if m.status == "finished" and m.api_fixture_id is not None:
            continue  # API sonucu esas alınır
        m.kickoff_utc = r["kickoff_utc"]
        m.status = "finished"
        m.home_goals, m.away_goals = r["fthg"], r["ftag"]
        n += 1
    session.commit()
    return n


def _fetch(url, dest, max_age, fetch, rep: EspnReport, max_requests: int) -> str | None:
    if not is_fresh(dest, max_age):
        if rep.requests >= max_requests:
            rep.stopped = True
            return None
        rep.requests += 1
    return fetch(url, dest, max_age)


def ingest_espn(session, today: date, codes: list[str] | None = None, seasons: list[int] | None = None,
                recent_days: int | None = None, fetch=fetch_text, max_requests: int = 150) -> EspnReport:
    """ESPN'den biten maçları yükler.

    seasons verilmezse bu sezon. recent_days verilirse sadece son o kadar günün maç günleri (günlük iş).
    max_requests: bu çalışmada en fazla kaç dosya indirilir (önbellekteki dosyalar sayılmaz).
    """
    rep = EspnReport()
    seed_aliases(session)  # aliases.csv'deki "espn" satırları (ör. Erzurum BB → BB Erzurumspor)
    current = current_season_start(today)
    seasons = seasons or [current]
    done = set(get_value(session, DONE_KEY) or [])
    leagues = [lg for lg in CLUB_LEAGUES if lg.code in SLUGS and (codes is None or lg.code in codes)]
    for lg in leagues:
        comp = session.query(Competition).filter_by(code=lg.code).one()
        slug = SLUGS[lg.code]
        for season in seasons:
            if rep.stopped:
                break
            probe = date(season, 10, 15) if season < current or today >= date(season, 10, 15) else today
            try:
                text = _fetch(day_url(slug, probe), ESPN_DIR / f"{lg.code}_{season}_takvim.json",
                              None if season < current else timedelta(days=1), fetch, rep, max_requests)
                if text is None:
                    break
                got_season, calendar, _ = parse_scoreboard(text)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                rep.errors.append(f"{lg.name} {season} takvim: {exc}")
                continue
            if got_season is not None and got_season != season:
                rep.errors.append(f"{lg.name} {season}: ESPN başka sezon döndürdü ({got_season})")
                continue
            days = [d for d in calendar if d < today and f"{lg.code}:{d:%Y%m%d}" not in done]
            if recent_days is not None:
                days = [d for d in days if d >= today - timedelta(days=recent_days)]
            rows, finished_days = [], []
            for d in days:
                settled = today - d >= SETTLED_AFTER
                try:
                    text = _fetch(day_url(slug, d), ESPN_DIR / lg.code / f"{d:%Y%m%d}.json",
                                  None if settled else timedelta(hours=6), fetch, rep, max_requests)
                except (httpx.HTTPError, ValueError) as exc:
                    rep.errors.append(f"{lg.name} {d}: {exc}")
                    continue
                if text is None:
                    break
                _, _, events = parse_scoreboard(text)
                rep.days += 1
                rows += [e for e in events if e["completed"] and e["season"] in (None, season)]
                if settled and all(e["completed"] for e in events):
                    finished_days.append(f"{lg.code}:{d:%Y%m%d}")
            if not rows:
                done |= set(finished_days)
                continue
            names = list(dict.fromkeys([r["home"] for r in rows] + [r["away"] for r in rows]))
            ids, unresolved = resolve_names(session, comp, season, names, lg.country)
            rep.unresolved += [f"{lg.name} {season}: {n}" for n in unresolved]
            rep.results += _upsert(session, comp, season, rows, ids)
            if not unresolved:  # eşleşmeyen ad varsa o günler bir dahaki sefere yeniden işlensin
                done |= set(finished_days)
    set_value(session, DONE_KEY, sorted(done))
    return rep


# ---------------------------------------------------------------- maç ayrıntısı: istatistik ve ilk 11

STAT_KEYS = {"totalShots": "shots", "shotsOnTarget": "sot", "possessionPct": "poss", "wonCorners": "corners",
             "foulsCommitted": "fouls", "yellowCards": "yellow", "redCards": "red"}


def summary_url(slug: str, espn_id: str) -> str:
    return f"{BASE}/{slug}/summary?event={espn_id}"


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
SLIM_ABOVE = 50_000  # bundan büyük önbellek dosyası küçük kopyasıyla değiştirilir


def parse_summary(text: str) -> dict:
    """Maç ayrıntısından ev/deplasman istatistikleri ve ilk 11.

    Dönen sözlük Match.stats'e eklenir: {"shots": [ev, dep], "sot": [...], "poss": [...], "corners": [...],
    "fouls": [...], "yellow": [...], "red": [...], "xi_home": [[id, ad, mevki], ...], "xi_away": [...]}.
    Eksik olan alan yazılmaz. Hiç istatistik yoksa {"summary": "yok"} döner (bir daha istenmesin diye).
    """
    data = json.loads(text)
    out: dict = {}
    side_of = {}
    for c in ((data.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []:
        side_of[str((c.get("team") or {}).get("id"))] = c.get("homeAway")
    vals: dict[str, dict] = {}
    for t in (data.get("boxscore") or {}).get("teams") or []:
        side = side_of.get(str((t.get("team") or {}).get("id")))
        if side not in ("home", "away"):
            continue
        for st in t.get("statistics") or []:
            key = STAT_KEYS.get(st.get("name"))
            if key:
                vals.setdefault(key, {})[side] = _num(st.get("displayValue"))
    for key, v in vals.items():
        if v.get("home") is not None and v.get("away") is not None:
            out[key] = [v["home"], v["away"]]
    for r in data.get("rosters") or []:
        side = r.get("homeAway") or side_of.get(str((r.get("team") or {}).get("id")))
        xi = [[str(p["athlete"].get("id")), p["athlete"].get("displayName"), (p.get("position") or {}).get("abbreviation")]
              for p in r.get("roster") or [] if p.get("starter") and p.get("athlete")]
        if side in ("home", "away") and len(xi) >= 10:
            out[f"xi_{side}"] = xi
    out.update(parse_odds(data))
    return out or {"summary": "yok"}


_B365 = {"preMatchFullTimeResultHome": "MS1", "preMatchFullTimeResultDraw": "MSX",
         "preMatchFullTimeResultAway": "MS2", "preMatchDoubleChanceHomeOrDraw": "CS1X",
         "preMatchDoubleChanceHomeOrAway": "CS12", "preMatchDoubleChanceDrawOrAway": "CSX2"}
_OU_LINES = {"1.5": "15", "2.5": "25", "3.5": "35"}


def _american(v) -> float | None:
    """Amerikan oranını (+400, -160) ondalık orana çevirir."""
    try:
        x = float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if x >= 100:
        return round(1 + x / 100, 3)
    if x <= -100:
        return round(1 + 100 / -x, 3)
    return None


def _fraction(v) -> float | None:
    """İngiliz oranını ("17/4") ondalık orana çevirir."""
    try:
        a, b = str(v).split("/")
        return round(1 + float(a) / float(b), 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def parse_odds(data: dict) -> dict:
    """Maç öncesi oranlar (ondalık): {"odds_dk": {"open": {...}, "close": {...}}, "odds_b365": {...}}.

    DraftKings için açılış ve kapanış 1X2 ile alt/üst; Bet365 için maç öncesi 1X2, çifte şans ve alt/üst.
    Alt/üstte yalnızca 1.5/2.5/3.5 çizgileri alınır (tam sayılı çizgide iade olur, bizim marketlerimize uymaz).
    """
    out: dict = {}
    for o in data.get("odds") or []:
        name = (o.get("provider") or {}).get("name") or ""
        if name == "DraftKings":
            dk = {}
            ml, tot = o.get("moneyline") or {}, o.get("total") or {}
            for when in ("open", "close"):
                row = {}
                for side, key in (("home", "MS1"), ("draw", "MSX"), ("away", "MS2")):
                    odd = _american(((ml.get(side) or {}).get(when) or {}).get("odds"))
                    if odd:
                        row[key] = odd
                for side, key in (("over", "UST"), ("under", "ALT")):
                    q = (tot.get(side) or {}).get(when) or {}
                    line = _OU_LINES.get(str(q.get("line") or "")[1:])
                    odd = _american(q.get("odds"))
                    if line and odd:
                        row[key + line] = odd
                if row:
                    dk[when] = row
            if dk:
                out["odds_dk"] = dk
        elif name.startswith("Bet 365"):
            team = ((o.get("bettingOdds") or {}).get("teamOdds")) or {}
            row = {}
            for field_, key in _B365.items():
                odd = _fraction((team.get(field_) or {}).get("value"))
                if odd:
                    row[key] = odd
            line = _OU_LINES.get(str((team.get("preMatchOverUnderHandicap") or {}).get("value") or ""))
            for field_, key in (("preMatchGoalLineOver", "UST"), ("preMatchGoalLineUnder", "ALT")):
                odd = _fraction((team.get(field_) or {}).get("value"))
                if line and odd:
                    row[key + line] = odd
            if row:
                out["odds_b365"] = row
    return out


def slim_summary(text: str) -> str:
    """Önbelleğe yazılacak küçük kopya: yalnızca parse_summary'nin okuduğu alanlar (dosya ~400 KB yerine ~10 KB)."""
    data = json.loads(text)
    comp = ((data.get("header") or {}).get("competitions") or [{}])[0]
    slim = {
        "header": {"competitions": [{"date": comp.get("date"), "competitors": [
            {"homeAway": c.get("homeAway"), "team": {"id": (c.get("team") or {}).get("id")}}
            for c in comp.get("competitors") or []]}]},
        "boxscore": {"teams": [
            {"team": {"id": (t.get("team") or {}).get("id")},
             "statistics": [{"name": s.get("name"), "displayValue": s.get("displayValue")}
                            for s in t.get("statistics") or []]}
            for t in (data.get("boxscore") or {}).get("teams") or []]},
        "rosters": [
            {"homeAway": r.get("homeAway"), "team": {"id": (r.get("team") or {}).get("id")},
             "roster": [{"starter": True, "athlete": {"id": (p.get("athlete") or {}).get("id"),
                                                      "displayName": (p.get("athlete") or {}).get("displayName")},
                         "position": {"abbreviation": (p.get("position") or {}).get("abbreviation")}}
                        for p in r.get("roster") or [] if p.get("starter") and p.get("athlete")]}
            for r in data.get("rosters") or []],
        "odds": [],
    }
    for o in data.get("odds") or []:
        name = (o.get("provider") or {}).get("name") or ""
        if name == "DraftKings":
            slim["odds"].append({"provider": {"name": name}, "moneyline": o.get("moneyline"), "total": o.get("total")})
        elif name.startswith("Bet 365"):
            team = ((o.get("bettingOdds") or {}).get("teamOdds")) or {}
            slim["odds"].append({"provider": {"name": name}, "bettingOdds": {"teamOdds": {
                k: {"value": v.get("value")} for k, v in team.items() if k.startswith("preMatch")}}})
    return json.dumps(slim, ensure_ascii=False, separators=(",", ":"))


@dataclass
class SummaryReport:
    requests: int = 0
    matches: int = 0
    with_stats: int = 0
    with_lineups: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: bool = False


def ingest_summaries(session, codes: list[str] | None = None, seasons: list[int] | None = None,
                     fetch=fetch_text, max_requests: int = 100, workers: int = 4) -> SummaryReport:
    """ESPN numarası bilinen, bitmiş ve ayrıntısı henüz alınmamış maçların istatistik ve ilk 11'ini yazar.

    Bitmiş maçın ayrıntısı değişmez; dosya bir kez indirilir (`data/raw/espn/ozet/`). İndirmeler `workers`
    kadar paralel yapılır (ESPN'i yormamak için az tutulur); veritabanına yazma tek iş parçacığında.
    """
    from concurrent.futures import ThreadPoolExecutor

    rep = SummaryReport()
    leagues = {lg.code: lg for lg in CLUB_LEAGUES if lg.code in SLUGS and (codes is None or lg.code in codes)}
    comps = {c.id: c.code for c in session.query(Competition).filter(Competition.code.in_(list(leagues)))}
    q = session.query(Match).filter(Match.competition_id.in_(list(comps)), Match.status == "finished")
    if seasons:
        q = q.filter(Match.season.in_(seasons))
    todo = []
    for m in q.order_by(Match.kickoff_utc):
        st = m.stats or {}
        if st.get("espn_id") and "summary" not in st:
            todo.append(m)
    jobs = []
    for m in todo:
        code = comps[m.competition_id]
        dest = ESPN_DIR / "ozet" / code / f"{m.stats['espn_id']}.json"
        if not is_fresh(dest, None):
            if rep.requests >= max_requests:
                rep.stopped = True
                break
            rep.requests += 1
        jobs.append((m, summary_url(SLUGS[code], m.stats["espn_id"]), dest))

    def get(job):
        m, url, dest = job
        try:
            return m, dest, fetch(url, dest, None), None
        except (httpx.HTTPError, OSError) as exc:
            return m, dest, None, exc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (m, dest, text, exc) in enumerate(pool.map(get, jobs), 1):
            if exc is not None:
                rep.errors.append(f"{m.id}: {exc}")
                continue
            try:
                parsed = parse_summary(text)
                if len(text) > SLIM_ABOVE:
                    dest.write_text(slim_summary(text), encoding="utf-8")
            except (ValueError, KeyError, TypeError) as e:
                rep.errors.append(f"{m.id}: çözülemedi ({e})")
                continue
            parsed.setdefault("summary", "ok")
            m.stats = {**(m.stats or {}), **parsed}
            rep.matches += 1
            rep.with_stats += "sot" in parsed
            rep.with_lineups += "xi_home" in parsed and "xi_away" in parsed
            if i % 200 == 0:
                session.commit()
    session.commit()
    return rep
