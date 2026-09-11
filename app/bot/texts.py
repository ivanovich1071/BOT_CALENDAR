"""Все тексты бота в одном месте — заказчик правит их, не трогая логику.

Приветствие и «Информация» собираются из профиля компании (админка → «Компания»),
константы ниже — запасной вариант, пока профиль не заполнен.
"""

from html import escape

WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

BTN_BOOK = "📅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_INFO = "ℹ️ Информация"
BTN_PHONE = "📱 Отправить телефон"
BTN_SKIP = "Пропустить"

GREETING = (
    "Здравствуйте, {name}!\n\n"
    "Я помогу записаться к специалисту: покажу свободное время, "
    "запишу и напомню о визите.\n\n"
    "Выберите действие в меню ниже."
)

ASK_PHONE = (
    "Оставьте номер телефона — специалист сможет связаться с вами, "
    "если планы изменятся. Это необязательно."
)
PHONE_SAVED = "Спасибо, номер сохранён."
PHONE_SKIPPED = "Хорошо, обойдёмся без телефона."

INFO = (
    "<b>Как это работает</b>\n\n"
    "📅 <b>Записаться</b> — выберите услугу, специалиста, дату и время. "
    "Свободное время показывается по календарю специалиста, поэтому занятых слотов в списке нет.\n\n"
    "📋 <b>Мои записи</b> — там же можно перенести или отменить визит.\n\n"
    "✍️ Можно и просто написать, например: «хочу на консультацию в пятницу после обеда» — "
    "я подберу свободное время."
)

HOW_IT_WORKS = (
    "📅 <b>Записаться</b> — выберите услугу, специалиста, дату и время.\n"
    "📋 <b>Мои записи</b> — перенос и отмена.\n"
    "✍️ Или просто напишите вопрос своими словами — отвечу и подберу время."
)


def greeting(company: dict, name: str) -> str:
    """Приветствие из профиля компании; {name} подставляется, остальной текст экранируется."""
    template = escape(company.get("greeting") or GREETING, quote=False)
    try:
        return template.format(name=escape(name, quote=False))
    except (KeyError, IndexError, ValueError):
        return template  # в тексте из админки фигурные скобки без {name}


def info(company: dict, services: list[dict]) -> str:
    """«Информация»: о компании, на что записаться, контакты, как пользоваться ботом."""
    if not company.get("name"):
        return INFO
    parts = [f"<b>{escape(company['name'], quote=False)}</b>"]
    if company.get("tagline"):
        parts[0] += f"\n<i>{escape(company['tagline'], quote=False)}</i>"
    if company.get("description"):
        parts.append(escape(company["description"], quote=False))
    if services:
        lines = [
            f"• {escape(s['name'], quote=False)} — {s['duration']} мин, {s['price_label']}"
            for s in services
        ]
        parts.append("<b>На что можно записаться</b>\n" + "\n".join(lines))
    contacts = [
        f"{icon} {escape(company[key], quote=False)}"
        for icon, key in (("📞", "phone"), ("✈️", "telegram"), ("🌐", "website"), ("✉️", "email"))
        if company.get(key)
    ]
    if contacts:
        parts.append("\n".join(contacts))
    parts.append(HOW_IT_WORKS)
    return "\n\n".join(parts)


CHOOSE_SERVICE = "Выберите услугу:"
CHOOSE_EMPLOYEE = "Выберите специалиста:"
CHOOSE_DAY = "Выберите дату:"
CHOOSE_SLOT = "Свободное время на {date}:"

NO_SERVICES = "Пока не настроено ни одной услуги. Загляните позже."
NO_EMPLOYEES = "Пока нет специалистов с расписанием. Загляните позже."
NO_SLOTS = "На {date} свободного времени нет. Выберите другую дату."
NO_BOOKINGS = "У вас пока нет активных записей."

CONFIRM = (
    "Проверьте запись:\n\n"
    "👤 {employee}\n"
    "💼 {service}\n"
    "📅 {date}\n"
    "🕐 {start} – {end}\n\n"
    "Всё верно?"
)

BOOKED = (
    "✅ Записал вас.\n\n"
    "👤 {employee}\n"
    "💼 {service}\n"
    "📅 {date}\n"
    "🕐 {start} – {end}\n\n"
    "Запись видна в разделе «Мои записи» — там же можно перенести или отменить."
)

SLOT_TAKEN = "Это время только что заняли. Выберите, пожалуйста, другое."
BOOKING_FAILED = "Не получилось оформить запись. Попробуйте ещё раз или напишите нам."

MY_BOOKINGS = "Ваши записи:"
BOOKING_CARD = "👤 {employee}\n💼 {service}\n📅 {date}\n🕐 {start} – {end}"

RESCHEDULE_CHOOSE_DAY = "Перенос записи с {date}, {start}.\n\nВыберите новую дату:"
RESCHEDULED = "✅ Перенёс запись.\n\n👤 {employee}\n💼 {service}\n📅 {date}\n🕐 {start} – {end}"

CANCEL_CONFIRM = "Отменить эту запись?\n\n👤 {employee}\n💼 {service}\n📅 {date}\n🕐 {start}"
CANCELLED = "Запись отменена. Будем рады видеть вас в другой раз."

NOT_FOUND = "Запись не найдена — возможно, она уже отменена."
CANCELLED_ACTION = "Отменено."
SOMETHING_WRONG = "Что-то пошло не так. Попробуйте ещё раз."

BTN_BACK = "← Назад"
BTN_YES = "✅ Подтвердить"
BTN_NO = "← Отмена"
BTN_RESCHEDULE = "🔄 Перенести"
BTN_CANCEL_BOOKING = "❌ Отменить"


def human_date(day) -> str:
    """10 сентября, четверг"""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}, {WEEKDAYS_RU[day.weekday()]}"


# ==== Напоминания ====

REMINDER = (
    "⏰ Напоминаю: {when} у вас запись.\n\n"
    "👤 {employee}\n"
    "💼 {service}\n"
    "📅 {date}\n"
    "🕐 {start} – {end}\n\n"
    "Планы изменились — перенесите или отмените кнопкой ниже."
)


def reminder_when(hours: int, days_ahead: int) -> str:
    """Как назвать момент визита: «через час», «завтра», «через 3 дн.»."""
    if hours < 24:
        return "через час" if hours == 1 else f"через {hours} ч"
    if days_ahead == 1:
        return "завтра"
    if days_ahead == 2:
        return "послезавтра"
    return f"через {days_ahead} дн."


# ==== Свободный текст (AI) ====

AI_DISABLED = "Пока я понимаю только кнопки меню — выберите действие ниже 👇"
AI_RATE_LIMITED = "Слишком много сообщений подряд. Воспользуйтесь, пожалуйста, кнопками меню 👇"
AI_NOT_UNDERSTOOD = (
    "Не совсем понял. Я записываю к специалисту — напишите, например, "
    "«хочу на консультацию в пятницу после обеда», или воспользуйтесь кнопками 👇"
)
AI_CHOOSE_SERVICE = "Понял, записываю. Выберите услугу:"
AI_FOUND_SLOTS = "Нашёл свободное время: {service}, {employee}, {date}.\nВыберите удобное:"
AI_WINDOW_EMPTY = "В это время на {date} всё занято. Вот что свободно в этот день:"
