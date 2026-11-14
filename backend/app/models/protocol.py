from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.types import JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow


class HospitalProtocol(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hospital_protocols"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PRT"))
    name: Mapped[str] = mapped_column(String(200), unique=True)
    category: Mapped[str] = mapped_column(String(60), index=True)
    responsible_department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    versions: Mapped[list["ProtocolVersion"]] = relationship(back_populates="protocol", cascade="all, delete-orphan",
                                                             order_by="ProtocolVersion.created_at")


class ProtocolVersion(Base):
    __tablename__ = "protocol_versions"
    __table_args__ = (UniqueConstraint("protocol_id", "version", name="protocol_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PRV"))
    protocol_id: Mapped[str] = mapped_column(ForeignKey("hospital_protocols.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rules: Mapped[dict] = mapped_column(JSONType, default=dict)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    protocol: Mapped[HospitalProtocol] = relationship(back_populates="versions")
