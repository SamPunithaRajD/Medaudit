from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.types import JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow

# Account lifecycle. Only `active` accounts can hold sessions.
ACCOUNT_STATUSES = ("pending_activation", "active", "suspended", "deactivated", "archived")


class RoleModel(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("USR"))
    # Sign-in identifier: the Doctor ID for doctors (e.g. DOC1001), an admin ID for administrators.
    # Stored upper-case; staff without one can sign in with their email.
    login_id: Mapped[str | None] = mapped_column(String(40), unique=True, index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # null until activation
    role: Mapped[str] = mapped_column(String(40), ForeignKey("roles.name"), index=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    is_activated: Mapped[bool] = mapped_column(Boolean, default=True)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=utcnow)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    doctor_profile: Mapped["DoctorProfile | None"] = relationship(
        back_populates="user", uselist=False, foreign_keys="DoctorProfile.user_id")


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (UniqueConstraint("token_hash"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("SES"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    remember: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class DoctorProfile(Base, TimestampMixin):
    """Administrator-provisioned doctor record. Name, email and department live on `users`."""
    __tablename__ = "doctor_profiles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("DPR"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True, index=True)
    designation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contact_info: Mapped[str | None] = mapped_column(String(500), nullable=True)
    employee_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    activation_policy: Mapped[str] = mapped_column(String(30), default="activation_code")
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    password_reset_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="doctor_profile", foreign_keys=[user_id])


class AccountToken(Base):
    """One-time activation / password-reset credential. Only an HMAC of the code is stored."""
    __tablename__ = "account_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("TOK"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20), index=True)  # activation | password_reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    theme: Mapped[str] = mapped_column(String(10), default="system")
    language: Mapped[str] = mapped_column(String(10), default="en")
    notification_preferences: Mapped[dict] = mapped_column(JSONType, default=dict)
    dashboard_preferences: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AuthSettings(Base):
    """Hospital-level authentication policy (single row, id=1), editable by administrators."""
    __tablename__ = "auth_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    activation_token_hours: Mapped[int] = mapped_column(Integer, default=72)
    reset_token_hours: Mapped[int] = mapped_column(Integer, default=24)
    max_failed_logins: Mapped[int] = mapped_column(Integer, default=5)
    lockout_minutes: Mapped[int] = mapped_column(Integer, default=15)
    max_activation_attempts: Mapped[int] = mapped_column(Integer, default=5)
    remember_me_days: Mapped[int] = mapped_column(Integer, default=7)
    allow_forgot_password_requests: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_emergency_access: Mapped[bool] = mapped_column(Boolean, default=True)
    emergency_access_max_minutes: Mapped[int] = mapped_column(Integer, default=240)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
