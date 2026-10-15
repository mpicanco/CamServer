"""Unit tests for the RTSP port scanner."""

from __future__ import annotations

import asyncio

from camserver import netscan


def test_hosts_in_network_slash30():
    hosts = netscan._hosts_in_network("192.168.1.0/30")
    assert hosts == ["192.168.1.1", "192.168.1.2"]


def test_hosts_in_network_skips_network_and_broadcast():
    hosts = netscan._hosts_in_network("10.0.0.0/24")
    assert len(hosts) == 254
    assert "10.0.0.0" not in hosts
    assert "10.0.0.255" not in hosts


def test_dedupe_prefers_lowest_port():
    candidates = [
        ("192.168.1.10", 8554),
        ("192.168.1.10", 554),
        ("192.168.1.11", 554),
    ]
    result = netscan._dedupe(candidates)
    assert [(c.ip, c.port) for c in result] == [
        ("192.168.1.10", 554),
        ("192.168.1.11", 554),
    ]


def test_detect_subnets_returns_cidrs():
    subnets = netscan.detect_subnets()
    assert isinstance(subnets, list)
    assert subnets
    assert all("/" in s for s in subnets)


async def test_is_open_true_for_listening_port():
    sem = asyncio.Semaphore(10)
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await netscan._is_open("127.0.0.1", port, 1.0, sem) is True
    finally:
        server.close()
        await server.wait_closed()


async def test_is_open_false_for_closed_port():
    sem = asyncio.Semaphore(10)
    assert await netscan._is_open("127.0.0.1", 1, 1.0, sem) is False
