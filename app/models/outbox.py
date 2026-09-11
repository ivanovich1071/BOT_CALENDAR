from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class OutboxMessage(Base):
    """Сообщение в Telegram, ожидающее отправки (уведомления сотрудникам).

    Кладётся в той же транзакции, что и изменение записи; отправляет задача
    планировщика — сбой Telegram не ломает запись и не теряет уведомление.
    """

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
