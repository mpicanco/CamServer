"""TCP port scanning for RTSP cameras on the local network.

Plain RTSP cameras have no discovery protocol, so the only way to find them is
to probe the well-known RTSP ports (554, and 8554 as a common alternative) on
each address of the local subnet(s).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
from dataclasses import dataclass

logger = logging.getLogger(__name__)

RTSP_PORTS = (554, 8554)
DEFAULT_TIMEOUT = 1.0
DEFAULT_CONCURRENCY = 256
MAX_HOSTS_PER_SUBNET = 2048


@dataclass
class RtspCandidate:
    ip: str
    port: int


async def _is_open(host: str, port: int, timeout: float, sem: asyncio.Semaphore) -> bool:
    async with sem:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            return False


def _hosts_in_network(network: str) -> list[str]:
    net = ipaddress.ip_network(network, strict=False)
    return [str(ip) for ip in net.hosts()]


def _dedupe(candidates: list[tuple[str, int]]) -> list[RtspCandidate]:
    """Keep a single entry per IP, preferring the lowest port number."""
    best: dict[str, int] = {}
    for host, port in candidates:
        if host not in best or port < best[host]:
            best[host] = port
    return [RtspCandidate(ip=h, port=p) for h, p in sorted(best.items())]


async def scan_rtsp(
    subnets: list[str],
    ports: tuple[int, ...] = RTSP_PORTS,
    timeout: float = DEFAULT_TIMEOUT,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[RtspCandidate]:
    sem = asyncio.Semaphore(concurrency)
    targets: list[tuple[str, int]] = []

    for network in subnets:
        try:
            hosts = _hosts_in_network(network)
        except ValueError:
            logger.warning("invalid subnet %r, skipping", network)
            continue
        if len(hosts) > MAX_HOSTS_PER_SUBNET:
            logger.warning(
                "subnet %s has %d hosts (> %d), skipping; set CAMSERVER_SCAN_SUBNETS "
                "to a smaller range",
                network,
                len(hosts),
                MAX_HOSTS_PER_SUBNET,
            )
            continue
        for host in hosts:
            for port in ports:
                targets.append((host, port))

    results: list[tuple[str, int]] = []

    async def check(host: str, port: int) -> None:
        if await _is_open(host, port, timeout, sem):
            results.append((host, port))

    await asyncio.gather(*(check(h, p) for h, p in targets))
    return _dedupe(results)


def _linux_subnets() -> list[str]:
    """Enumerate IPv4 subnets from local interfaces via ioctl (Linux)."""
    import fcntl

    subnets: list[str] = []
    try:
        names = socket.if_nameindex()
    except OSError:
        return subnets

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _, ifname in names:
            if ifname == "lo":
                continue
            try:
                ifreq = struct.pack("256s", ifname.encode("utf-8")[:15])
                addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, ifreq)[20:24])
                netmask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x891B, ifreq)[20:24])
                subnets.append(str(ipaddress.ip_network(f"{addr}/{netmask}", strict=False)))
            except OSError:
                continue
    finally:
        s.close()
    return subnets


def _primary_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def detect_subnets() -> list[str]:
    subnets = _linux_subnets()
    if subnets:
        return subnets
    return [f"{_primary_ip()}/24"]
