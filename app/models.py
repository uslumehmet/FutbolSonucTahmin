"""Veritabanı tabloları (SQLAlchemy 2)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator):
    """Naive UTC olarak saklar, UTC'ye bağlı (aware) datetime döndürür."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime kaydedilemez; UTC kullanın")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Competition(Base):
    __tablename__ = "competition"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(16))
    api_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Team(Base):
    __tablename__ = "team"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    country: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_temp: Mapped[bool] = mapped_column(Boolean, default=False)
    api_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class TeamAlias(Base):
    __tablename__ = "team_alias"
    __table_args__ = (UniqueConstraint("source", "alias_norm"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(8))
    alias_norm: Mapped[str] = mapped_column(String(80))


class Match(Base):
    __tablename__ = "match"
    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    kickoff_utc: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), index=True)
    neutral: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="scheduled", index=True)
    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    api_fixture_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    unmatched: Mapped[bool] = mapped_column(Boolean, default=False)

    competition: Mapped[Competition] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class OddsQuote(Base):
    __tablename__ = "odds_quote"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id", ondelete="CASCADE"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(8))
    market: Mapped[str] = mapped_column(String(8))
    odds: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(12))


class MissingPlayer(Base):
    __tablename__ = "missing_player"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    player_name: Mapped[str] = mapped_column(String(80))
    api_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attack_effect: Mapped[float | None] = mapped_column(Float, nullable=True)
    concede_effect: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(8))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PlayerSeasonStat(Base):
    __tablename__ = "player_season_stat"
    api_player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[str] = mapped_column(String(2))
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    team_games: Mapped[int] = mapped_column(Integer, default=0)
    team_goals: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Prediction(Base):
    __tablename__ = "prediction"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    lam_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    lam_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    probs: Mapped[dict] = mapped_column(JSON, default=dict)
    layers: Mapped[dict] = mapped_column(JSON, default=dict)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    missing_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    top_scores: Mapped[list] = mapped_column(JSON, default=list)


class Wallet(Base):
    __tablename__ = "wallet"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    owner: Mapped[str] = mapped_column(String(8))
    start_balance: Mapped[int] = mapped_column(Integer)
    balance: Mapped[int] = mapped_column(Integer)
    stake_rule: Mapped[str] = mapped_column(String(8), default="percent")
    stake_value: Mapped[float] = mapped_column(Float, default=3.0)
    busted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Coupon(Base):
    __tablename__ = "coupon"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(8))
    target_odds: Mapped[float] = mapped_column(Float)
    total_odds: Mapped[float] = mapped_column(Float)
    prob: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)
    settled_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    play_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    picks: Mapped[list["CouponPick"]] = relationship(
        back_populates="coupon", cascade="all, delete-orphan", order_by="CouponPick.id"
    )


class CouponPick(Base):
    __tablename__ = "coupon_pick"
    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupon.id", ondelete="CASCADE"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    odds: Mapped[float] = mapped_column(Float)
    prob: Mapped[float] = mapped_column(Float)
    result: Mapped[str] = mapped_column(String(8), default="pending", index=True)

    coupon: Mapped[Coupon] = relationship(back_populates="picks")
    match: Mapped[Match] = relationship()


class Bet(Base):
    __tablename__ = "bet"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id", ondelete="CASCADE"), index=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupon.id"), index=True)
    stake: Mapped[int] = mapped_column(Integer)
    payout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    coupon: Mapped[Coupon] = relationship()
    wallet: Mapped[Wallet] = relationship()


class SinglePick(Base):
    __tablename__ = "single_pick"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    odds: Mapped[float] = mapped_column(Float)
    prob: Mapped[float] = mapped_column(Float)
    play_date: Mapped[date] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(8), default="pending", index=True)

    match: Mapped[Match] = relationship()


class ApiCall(Base):
    __tablename__ = "api_call"
    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(40))
    params_key: Mapped[str] = mapped_column(String(300), index=True)
    called_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status_code: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text)


class JobRun(Base):
    __tablename__ = "job_run"
    __table_args__ = (UniqueConstraint("job", "run_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(20))
    run_key: Mapped[str] = mapped_column(String(20))
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SettingRow(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    report: Mapped[dict] = mapped_column(JSON)


class ModelBlob(Base):
    """Eğitilmiş model dosyaları. Bulutta disk kalıcı olmadığı için veritabanında tutulur."""
    __tablename__ = "model_blob"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
