"""Model önbelleği: gün başına yarışma modeli, Elo, özellikler ve Katman 2."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pandas as pd

from app.config import MODELS_DIR, TZ, current_season_start, get_value
from app.model.dixon_coles import DCModel, fit_dixon_coles
from app.model.elo import CLUB_HOME_ADV, CLUB_K, K_BY_TIER, NATIONAL_HOME_ADV, EloTable, elo_history
from app.model.features import build_features
from app.model.international import IntlGoalModel, fit_intl_goal_model
from app.model.layer2 import GROUPS, load_layer2, load_layer2_db
from app.models import Competition, Match

DC_SEASONS = 5
HALF_LIFE_DAYS = 365.0
NATIONAL_TIER_BY_CODE = {"WC": "WC", "WCQ_EU": "WCQ", "EURO": "EURO", "EUROQ": "EUROQ", "UNL": "UNL"}
LAYER2_PATH = MODELS_DIR / "layer2.pkl"


def day_start_utc(as_of: date) -> datetime:
    return datetime.combine(as_of, time(0, 0), tzinfo=TZ).astimezone(UTC)


def competition_frame(session, comp_ids: list[int]) -> pd.DataFrame:
    q = session.query(Match.id, Match.kickoff_utc, Match.home_team_id, Match.away_team_id, Match.home_goals,
                      Match.away_goals, Match.status, Match.season, Match.neutral, Match.stats,
                      Match.competition_id).filter(Match.competition_id.in_(comp_ids),
                                                   # geçici takımlı kayıt aynı maçın ikinci kopyası olabilir
                                                   Match.unmatched.isnot(True))
    rows = [dict(r._mapping) for r in q]
    df = pd.DataFrame(rows, columns=["id", "kickoff_utc", "home_team_id", "away_team_id", "home_goals", "away_goals",
                                     "status", "season", "neutral", "stats", "competition_id"])
    df = df.rename(columns={"home_team_id": "home_id", "away_team_id": "away_id"})
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"], utc=True)
    finished = df["status"] == "finished"
    df.loc[~finished, ["home_goals", "away_goals"]] = None
    df["home_goals"] = pd.to_numeric(df["home_goals"])
    df["away_goals"] = pd.to_numeric(df["away_goals"])
    return df.sort_values("kickoff_utc", kind="stable").reset_index(drop=True)


class ModelStore:
    def __init__(self, session, layer2_path=LAYER2_PATH):
        self.session = session
        self.layer2_path = layer2_path
        self._cache: dict = {}
        self._comp = {c.code: c for c in session.query(Competition)}

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def club_frame(self, code: str) -> pd.DataFrame:
        return self._memo(("frame", code), lambda: competition_frame(self.session, [self._comp[code].id]))

    def club_model(self, code: str, as_of: date) -> DCModel:
        def fit():
            df = self.club_frame(code)
            cutoff = day_start_utc(as_of)
            first = current_season_start(as_of) - (DC_SEASONS - 1)
            train = df[(df["season"] >= first) & df["home_goals"].notna()]
            prev = self._cache.get(("dc", code, as_of - timedelta(days=1)))
            return fit_dixon_coles(train, cutoff, half_life_days=HALF_LIFE_DAYS, init=prev)
        return self._memo(("dc", code, as_of), fit)

    def _club_elo_frame(self, code: str) -> tuple[pd.DataFrame, EloTable]:
        return self._memo(("elo_frame", code), lambda: elo_history(
            self.club_frame(code), home_adv=CLUB_HOME_ADV, k_fn=lambda r: CLUB_K))

    def club_elo(self, code: str, as_of: date) -> EloTable:
        # Bitmiş maçların hepsi as_of öncesidir (gelecek maçlar reytingi değiştirmez).
        return self._club_elo_frame(code)[1]

    def club_features(self, code: str, as_of: date) -> pd.DataFrame:
        return self._memo(("feat", code), lambda: build_features(self._club_elo_frame(code)[0]))

    def national_frame(self) -> pd.DataFrame:
        def build():
            codes = ["INTL_HIST"] + list(NATIONAL_TIER_BY_CODE)
            ids = {self._comp[c].id: c for c in codes if c in self._comp}
            df = competition_frame(self.session, list(ids))
            df["code"] = df["competition_id"].map(ids)
            df["tier"] = [
                (s or {}).get("tier") if c == "INTL_HIST" else NATIONAL_TIER_BY_CODE.get(c, "OTHER")
                for s, c in zip(df["stats"], df["code"])
            ]
            df["tier"] = df["tier"].fillna("OTHER")
            # API'den gelen milli maç, aynı maçın tarihsel kaydıyla çift sayılmasın
            api = df[df["code"] != "INTL_HIST"]
            keys = {(h, a, ko.date() + timedelta(days=d)) for h, a, ko in
                    zip(api["home_id"], api["away_id"], api["kickoff_utc"]) for d in (-1, 0, 1)}
            dup = [(c == "INTL_HIST") and ((h, a, ko.date()) in keys)
                   for c, h, a, ko in zip(df["code"], df["home_id"], df["away_id"], df["kickoff_utc"])]
            return df[~pd.Series(dup, index=df.index)].reset_index(drop=True)
        return self._memo(("nat_frame",), build)

    def national(self, as_of: date) -> tuple[EloTable, IntlGoalModel, dict[int, int]]:
        def fit():
            df = self.national_frame()
            cutoff = day_start_utc(as_of)
            past = df[df["kickoff_utc"] < cutoff]
            hist, table = elo_history(past, home_adv=NATIONAL_HOME_ADV, k_fn=lambda r: K_BY_TIER.get(r["tier"], 30))
            since = cutoff - timedelta(days=365 * 10)
            model = fit_intl_goal_model(hist, since=since)
            played = past[past["home_goals"].notna()]
            counts = pd.concat([played["home_id"], played["away_id"]]).value_counts().to_dict()
            return table, model, {int(k): int(v) for k, v in counts.items()}
        return self._memo(("nat", as_of), fit)

    def layer2(self) -> dict:
        def load():
            models = load_layer2_db(self.session)  # bulutta kalıcı kopya
            if models is None:
                models = load_layer2(self.layer2_path)
            active = get_value(self.session, "layer2_active", {}) or {}
            return {g: (models.get(g) if active.get(g) else None) for g in GROUPS}
        return self._memo(("l2",), load)
