from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JSONType, UTCDateTime
from app.utils.ids import utcnow


class AuditLog(Base):
    """Append-only, hash-chained security audit trail. `details` must never contain patient data."""
    __tablename__ = "audit_logs"
    seq: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(40), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    target_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))


@event.listens_for(AuditLog, "before_update")
def _no_update(mapper, connection, target):  # pragma: no cover - guarded path
    raise RuntimeError("audit_logs is append-only")


@event.listens_for(AuditLog, "before_delete")
def _no_delete(mapper, connection, target):  # pragma: no cover - guarded path
    raise RuntimeError("audit_logs is append-only")
