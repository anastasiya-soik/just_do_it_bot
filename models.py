from __future__ import annotations
import enum
from datetime import date
from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, String, Boolean, BigInteger, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ChallengeStatus(str, enum.Enum):
    active    = "active"
    completed = "completed"
    archived  = "archived"


class DayStatus(str, enum.Enum):
    success = "success"
    fail    = "fail"
    skip    = "skip"


class User(Base):
    __tablename__ = "users"

    id:               Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    telegram_id:      Mapped[int]        = mapped_column(BigInteger, unique=True, index=True)
    username:         Mapped[str | None] = mapped_column(String(255), nullable=True)
    utc_offset:       Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_time:      Mapped[str]        = mapped_column(String(8), default="21:00")
    silent_mode:      Mapped[bool]       = mapped_column(Boolean, default=False)

    # Явный Enum вместо String — SQLAlchemy валидирует значения на уровне ORM
    missed_day_policy: Mapped[DayStatus] = mapped_column(
        Enum(DayStatus, native_enum=False), default=DayStatus.skip
    )

    last_notified_at:    Mapped[date | None] = mapped_column(Date, nullable=True)
    last_weekly_stats_at:  Mapped[date | None] = mapped_column(Date, nullable=True)
    last_motivation_at:    Mapped[date | None] = mapped_column(Date, nullable=True)
    xp:               Mapped[int]         = mapped_column(Integer, default=0)

    # default=0: первая заморозка выдаётся за стрик 7 дней через check_milestone,
    # не авансом при регистрации
    freeze_count:    Mapped[int]  = mapped_column(Integer, default=0)
    premium_customs: Mapped[bool] = mapped_column(Boolean, default=False)

    challenges: Mapped[list["Challenge"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Challenge(Base):
    __tablename__ = "challenges"

    id:             Mapped[int]             = mapped_column(primary_key=True, autoincrement=True)
    user_id:        Mapped[int]             = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    challenge_type: Mapped[str]             = mapped_column(String(50))
    status:         Mapped[ChallengeStatus] = mapped_column(
        Enum(ChallengeStatus, native_enum=False), default=ChallengeStatus.active
    )
    start_date:     Mapped[date]            = mapped_column(Date)
    target_date:    Mapped[date | None]     = mapped_column(Date, nullable=True)
    completed_at:   Mapped[date | None]     = mapped_column(Date, nullable=True)
    current_streak: Mapped[int]             = mapped_column(Integer, default=0)
    longest_streak: Mapped[int]             = mapped_column(Integer, default=0)
    partner_challenge_id: Mapped[int | None] = mapped_column(
        ForeignKey("challenges.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped["User"]               = relationship(back_populates="challenges")
    days: Mapped[list["ChallengeDay"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan"
    )


class ChallengeDay(Base):
    __tablename__ = "challenge_days"

    id:           Mapped[int]       = mapped_column(primary_key=True, autoincrement=True)
    challenge_id: Mapped[int]       = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), index=True
    )
    date:         Mapped[date]      = mapped_column(Date, index=True)
    status:       Mapped[DayStatus] = mapped_column(Enum(DayStatus, native_enum=False))

    challenge: Mapped["Challenge"] = relationship(back_populates="days")

    __table_args__ = (
        UniqueConstraint("challenge_id", "date", name="_challenge_date_uc"),
    )


class PartnerInvite(Base):
    __tablename__ = "partner_invites"

    id:           Mapped[int]  = mapped_column(primary_key=True, autoincrement=True)
    token:        Mapped[str]  = mapped_column(String(32), unique=True, index=True)
    challenge_id: Mapped[int]  = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"))
    created_at:   Mapped[date] = mapped_column(Date)