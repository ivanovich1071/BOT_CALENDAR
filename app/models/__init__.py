"""Импорт всех моделей — регистрация в Base.metadata для Alembic."""

from app.models.ai_message import AiMessage
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.models.booking import Booking
from app.models.calendar import Calendar
from app.models.client import Client
from app.models.employee import Employee
from app.models.employee_service import employee_services
from app.models.google_account import GoogleAccount
from app.models.knowledge_article import KnowledgeArticle
from app.models.notification import Notification
from app.models.outbox import OutboxMessage
from app.models.schedule import Schedule
from app.models.schedule_exception import ScheduleException
from app.models.service import Service
from app.models.user import User

__all__ = [
    "AiMessage",
    "AppSetting",
    "AuditLog",
    "Booking",
    "Calendar",
    "Client",
    "Employee",
    "GoogleAccount",
    "KnowledgeArticle",
    "Notification",
    "OutboxMessage",
    "Schedule",
    "ScheduleException",
    "Service",
    "User",
    "employee_services",
]
