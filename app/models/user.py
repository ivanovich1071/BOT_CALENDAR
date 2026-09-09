from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    """Учётная запись для входа в админку (админ, менеджер, сотрудник, наблюдатель)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), default="employee")
    # Дополнительные гранулярные права поверх роли (см. models/enums.py)
    permissions: Mapped[list | None] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee = relationship("Employee", back_populates="user", uselist=False)

    def effective_permissions(self) -> set[str]:
        from app.models.enums import ROLE_DEFAULT_PERMISSIONS

        extra = set(self.permissions or [])
        return set(ROLE_DEFAULT_PERMISSIONS.get(self.role, ())) | extra

    def has_permission(self, perm: str) -> bool:
        return perm in self.effective_permissions()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.login} ({self.role})>"
