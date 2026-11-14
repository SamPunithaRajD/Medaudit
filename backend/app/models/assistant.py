from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.types import EncryptedText, JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow


class AssistantConversation(Base, TimestampMixin):
    __tablename__ = "assistant_conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CNV"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Conversation")

    messages: Mapped[list["AssistantMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan",
                                                              order_by="AssistantMessage.created_at")


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("MSG"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(EncryptedText)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence: Mapped[list] = mapped_column(JSONType, default=list)
    limitations: Mapped[list] = mapped_column(JSONType, default=list)
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_human_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")
