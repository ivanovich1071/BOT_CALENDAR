"""Порядок IP-адресов в исходящих соединениях."""

import socket

from app.config.network import ipv4_first


def _entry(family: int, host: str):
    return (family, socket.SOCK_STREAM, 6, "", (host, 443))


def test_ipv4_поднимается_выше_ipv6():
    """Регрессия: httplib2 брал первый адрес, а неотвечающий IPv6 стоял первым."""
    results = [
        _entry(socket.AF_INET6, "2001:4860:4842:400::"),
        _entry(socket.AF_INET6, "2001:4860:4843:400::"),
        _entry(socket.AF_INET, "172.217.116.4"),
    ]
    assert [item[0] for item in ipv4_first(results)] == [
        socket.AF_INET,
        socket.AF_INET6,
        socket.AF_INET6,
    ]


def test_ipv6_остаётся_запасным_вариантом():
    """Не выбрасываем IPv6: на хостах с рабочим IPv6 он должен оставаться доступен."""
    results = [_entry(socket.AF_INET6, "::1"), _entry(socket.AF_INET, "127.0.0.1")]
    assert len(ipv4_first(results)) == 2


def test_порядок_внутри_семейства_сохраняется():
    """Google балансирует нагрузку порядком адресов — внутри IPv4 его не трогаем."""
    results = [
        _entry(socket.AF_INET, "172.217.116.4"),
        _entry(socket.AF_INET, "172.217.118.4"),
    ]
    assert [item[4][0] for item in ipv4_first(results)] == ["172.217.116.4", "172.217.118.4"]


def test_пустой_список_не_ломает():
    assert ipv4_first([]) == []
