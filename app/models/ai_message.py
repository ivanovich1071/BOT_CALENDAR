from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class AiMessage(Base):
    """Реплика диалога клиента с ИИ-консультантом.

    role: user — клиент, assistant — ответ модели, tool — результат инструмента.
    Хранится ограниченное время (задача планировщика чистит старые).
    """

    __tablename__ = "ai_messages"
    __table_args__ = (Index("ix_ai_messages_client_created", "client_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client = relationship("Client")
