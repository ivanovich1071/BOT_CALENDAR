"""Порядок IP-адресов для исходящих соединений.

DNS Google отдаёт AAAA-записи первыми, и на машинах без рабочего IPv6-транзита
соединение с www.googleapis.com уходит в таймаут. httplib2, на котором работает
google-api-python-client, берёт первый адрес и до IPv4 не добирается — список
календарей не загружается, хотя токены уже получены.

Сортируем выдачу getaddrinfo так, чтобы IPv4 шёл первым. IPv6 остаётся в
списке запасным вариантом, поэтому на хостах с рабочим IPv6 поведение прежнее.
"""

import logging
import socket

logger = logging.getLogger(__name__)

_original_getaddrinfo = socket.getaddrinfo
_applied = False


def ipv4_first(results: list) -> list:
    """IPv4-адреса вперёд, порядок внутри каждого семейства сохраняется."""
    return sorted(results, key=lambda item: 0 if item[0] == socket.AF_INET else 1)


def prefer_ipv4() -> None:
    """Включает предпочтение IPv4 для всего процесса. Повторный вызов безвреден."""
    global _applied
    if _applied:
        return

    def patched_getaddrinfo(*args, **kwargs):
        return ipv4_first(_original_getaddrinfo(*args, **kwargs))

    socket.getaddrinfo = patched_getaddrinfo
    _applied = True
    logger.info("Исходящие соединения: IPv4 в приоритете")
