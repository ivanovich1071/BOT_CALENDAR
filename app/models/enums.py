"""Справочники ролей и прав. В БД хранятся строками (проще миграции)."""

ADMIN = "admin"
MANAGER = "manager"
EMPLOYEE = "employee"
VIEWER = "viewer"
ROLES = (ADMIN, MANAGER, EMPLOYEE, VIEWER)
ROLE_LABELS_RU = {
    ADMIN: "Администратор",
    MANAGER: "Менеджер",
    EMPLOYEE: "Сотрудник",
    VIEWER: "Наблюдатель",
}

# Гранулярные права
PERM_MANAGE_EMPLOYEES = "manage_employees"
PERM_MANAGE_SERVICES = "manage_services"
PERM_MANAGE_SCHEDULE = "manage_schedule"
PERM_VIEW_CALENDAR = "view_calendar"
# Без этого права сотрудник видит и меняет только своё: записи, расписание, клиентов
PERM_VIEW_ALL = "view_all"
PERM_CREATE_BOOKING = "create_booking"
PERM_EDIT_BOOKING = "edit_booking"
PERM_DELETE_BOOKING = "delete_booking"
PERM_VIEW_CLIENTS = "view_clients"
PERM_MANAGE_SETTINGS = "manage_settings"
PERM_VIEW_AUDIT = "view_audit"

ALL_PERMISSIONS = (
    PERM_MANAGE_EMPLOYEES,
    PERM_MANAGE_SERVICES,
    PERM_MANAGE_SCHEDULE,
    PERM_VIEW_CALENDAR,
    PERM_VIEW_ALL,
    PERM_CREATE_BOOKING,
    PERM_EDIT_BOOKING,
    PERM_DELETE_BOOKING,
    PERM_VIEW_CLIENTS,
    PERM_MANAGE_SETTINGS,
    PERM_VIEW_AUDIT,
)

PERMISSION_LABELS_RU = {
    PERM_VIEW_ALL: "Видит всех сотрудников (иначе — только себя)",
    PERM_MANAGE_EMPLOYEES: "Управление сотрудниками",
    PERM_MANAGE_SERVICES: "Управление услугами",
    PERM_MANAGE_SCHEDULE: "Управление расписанием",
    PERM_VIEW_CALENDAR: "Просмотр календаря",
    PERM_CREATE_BOOKING: "Создание записей",
    PERM_EDIT_BOOKING: "Изменение записей",
    PERM_DELETE_BOOKING: "Удаление записей",
    PERM_VIEW_CLIENTS: "Просмотр клиентов",
    PERM_MANAGE_SETTINGS: "Управление настройками",
    PERM_VIEW_AUDIT: "Просмотр журнала аудита",
}

# Права по умолчанию для роли (поверх них админ может выдавать отдельные права)
ROLE_DEFAULT_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ADMIN: ALL_PERMISSIONS,
    MANAGER: (
        PERM_MANAGE_SERVICES,
        PERM_MANAGE_SCHEDULE,
        PERM_VIEW_CALENDAR,
        PERM_VIEW_ALL,
        PERM_CREATE_BOOKING,
        PERM_EDIT_BOOKING,
        PERM_DELETE_BOOKING,
        PERM_VIEW_CLIENTS,
    ),
    # Сотрудник по умолчанию ведёт своё время и свои записи — только своё
    EMPLOYEE: (
        PERM_VIEW_CALENDAR,
        PERM_CREATE_BOOKING,
        PERM_EDIT_BOOKING,
        PERM_MANAGE_SCHEDULE,
        PERM_VIEW_CLIENTS,
    ),
    VIEWER: (PERM_VIEW_CALENDAR, PERM_VIEW_ALL),
}

# Статусы записи
BOOKED = "booked"
CANCELLED = "cancelled"
COMPLETED = "completed"
NO_SHOW = "no_show"
BOOKING_STATUSES = (BOOKED, CANCELLED, COMPLETED, NO_SHOW)

BOOKING_STATUS_LABELS_RU = {
    BOOKED: "Активна",
    CANCELLED: "Отменена",
    COMPLETED: "Завершена",
    NO_SHOW: "Неявка",
}

# Исключения расписания
EXC_DAY_OFF = "day_off"
EXC_BLOCK = "block"
EXC_EXTRA = "extra"
EXCEPTION_KINDS = (EXC_DAY_OFF, EXC_BLOCK, EXC_EXTRA)
EXCEPTION_KIND_LABELS_RU = {
    EXC_DAY_OFF: "Выходной / отпуск",
    EXC_BLOCK: "Закрыть время",
    EXC_EXTRA: "Дополнительное окно",
}

# Источник записи
SOURCE_TELEGRAM = "telegram"
SOURCE_ADMIN = "admin"
SOURCE_AI = "ai"
BOOKING_SOURCES = (SOURCE_TELEGRAM, SOURCE_ADMIN, SOURCE_AI)
