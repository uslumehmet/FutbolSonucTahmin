"""openfootball açık verisini (football.json ve Football.TXT) ayrıştırma."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

BASE = "https://raw.githubusercontent.com/openfootball"
JSON_SLUG = {"E0": "en.1", "SP1": "es.1", "I1": "it.1", "D1": "de.1", "F1": "fr.1"}
LEAGUE_TZ = {
    "E0": "Europe/London", "SP1": "Europe/Madrid", "I1": "Europe/Rome",
    "D1": "Europe/Berlin", "F1": "Europe/Paris", "T1": "Europe/Istanbul",
}
DEFAULT_TIME = {"T1": "19:00"}
HISTORY_START_YEAR = 2014
COLUMNS = ["kickoff_utc", "home", "away", "fthg", "ftag"]


def _season_dir(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def json_url(code: str, start_year: int) -> str:
    return f"{BASE}/football.json/master/{_season_dir(start_year)}/{JSON_SLUG[code]}.json"


def txt_url(start_year: int) -> str:
    return f"{BASE}/europe/master/turkey/{_season_dir(start_year)}_tr1.txt"


def _to_utc(day: str, hhmm: str, tz: str) -> datetime:
    local = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(tz))
    return pd.Timestamp(local).tz_convert("UTC").to_pydatetime()


def _ft(score) -> tuple[int, int] | None:
    if isinstance(score, dict):
        score = score.get("ft")
    if isinstance(score, (list, tuple)) and len(score) == 2 and all(isinstance(x, int) for x in score):
        return int(score[0]), int(score[1])
    return None


FIXTURE_COLUMNS = ["kickoff_utc", "home", "away"]


def _json_rows(text: str, tz: str, default_time: str):
    """(kickoff_utc, ev, deplasman, skor ya da None) — oynanmış ve oynanmamış bütün maçlar."""
    for m in json.loads(text).get("matches", []):
        if not m.get("date") or not m.get("team1") or not m.get("team2"):
            continue
        hhmm = (m.get("time") or default_time)[:5]
        if not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            hhmm = default_time
        yield _to_utc(m["date"], hhmm, tz), m["team1"].strip(), m["team2"].strip(), _ft(m.get("score"))


def parse_of_json(text: str, tz: str, default_time: str = "15:00") -> pd.DataFrame:
    rows = [{"kickoff_utc": ko, "home": h, "away": a, "fthg": ft[0], "ftag": ft[1]}
            for ko, h, a, ft in _json_rows(text, tz, default_time) if ft is not None]
    return pd.DataFrame(rows, columns=COLUMNS)


def parse_of_json_fixtures(text: str, tz: str, default_time: str = "15:00") -> pd.DataFrame:
    """Henüz oynanmamış (skorsuz) maçlar: sezonun ileri tarihli fikstürü."""
    rows = [{"kickoff_utc": ko, "home": h, "away": a}
            for ko, h, a, ft in _json_rows(text, tz, default_time) if ft is None]
    return pd.DataFrame(rows, columns=FIXTURE_COLUMNS)


_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DATE_RE = re.compile(r"^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\b")


def _infer_year(weekday: str, month: int, day: int, season_start: int, explicit: str | None) -> int:
    if explicit:
        return int(explicit)
    wanted = _WEEKDAYS.index(weekday)
    fits = []
    for y in (season_start, season_start + 1):
        try:
            if date(y, month, day).weekday() == wanted:
                fits.append(y)
        except ValueError:
            pass
    if len(fits) == 1:
        return fits[0]
    return season_start if month >= 7 else season_start + 1


_MATCH_RE = re.compile(
    r"^\s+(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>[^\s(].*?)\s+(?P<hg>\d{1,2})-(?P<ag>\d{1,2})"
    r"(?:\s+\(\d{1,2}-\d{1,2}\))?(?:\s+(?:aet|a\.e\.t\.|pen\.?[^A-Za-z]*))?\s{2,}(?P<away>\S.*?)\s*$"
)
# "    20:30  Ev Sahibi   v Deplasman   0-3 (0-3)" biçimi
_MATCH_V_RE = re.compile(
    r"^\s+(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>[^\s(].*?)\s+v\s+(?P<away>\S.*?)\s+"
    r"(?P<hg>\d{1,2})-(?P<ag>\d{1,2})(?:\s+\(\d{1,2}-\d{1,2}\))?.*$"
)


# "    20:30  Ev Sahibi   v Deplasman" biçiminde, skoru henüz olmayan maç
_FIXTURE_V_RE = re.compile(
    r"^\s+(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>[^\s(].*?)\s+v\s+(?P<away>[^\s(\[].*?)\s*$"
)
_SCORE_RE = re.compile(r"\d{1,2}-\d{1,2}")
_NOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*$")  # "Göztepe  [ertelendi]" gibi notlar


def _txt_rows(text: str, tz: str, season_start: int, default_time: str):
    """(kickoff_utc, ev, deplasman, skor ya da None) — oynanmış ve oynanmamış bütün maçlar."""
    day: str | None = None
    last_time = default_time
    for line in text.splitlines():
        dm = _DATE_RE.match(line)
        if dm:
            month, dnum = _MONTHS[dm.group(2)], int(dm.group(3))
            year = _infer_year(dm.group(1), month, dnum, season_start, dm.group(4))
            day = f"{year:04d}-{month:02d}-{dnum:02d}"
            last_time = default_time
            continue
        if day is None or not line.startswith(" "):
            continue
        mm = _MATCH_V_RE.match(line) or _MATCH_RE.match(line)
        if mm:
            score = (int(mm.group("hg")), int(mm.group("ag")))
        else:
            mm = _FIXTURE_V_RE.match(line)
            if not mm or _SCORE_RE.search(mm.group("away")):
                continue
            score = None
        if mm.group("time"):
            last_time = mm.group("time").replace(".", ":")
        away = mm.group("away").strip() if score is not None else _NOTE_RE.sub("", mm.group("away")).strip()
        yield _to_utc(day, last_time, tz), mm.group("home").strip(), away, score


def parse_of_txt(text: str, tz: str, season_start: int, default_time: str = "19:00") -> pd.DataFrame:
    rows = [{"kickoff_utc": ko, "home": h, "away": a, "fthg": sc[0], "ftag": sc[1]}
            for ko, h, a, sc in _txt_rows(text, tz, season_start, default_time) if sc is not None]
    return pd.DataFrame(rows, columns=COLUMNS)


def parse_of_txt_fixtures(text: str, tz: str, season_start: int, default_time: str = "19:00") -> pd.DataFrame:
    """Henüz oynanmamış (skorsuz) maçlar: sezonun ileri tarihli fikstürü."""
    rows = [{"kickoff_utc": ko, "home": h, "away": a}
            for ko, h, a, sc in _txt_rows(text, tz, season_start, default_time) if sc is None]
    return pd.DataFrame(rows, columns=FIXTURE_COLUMNS)
