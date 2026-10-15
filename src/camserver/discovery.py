"""ONVIF WS-Discovery multicast scanner (no third-party dependencies)."""

from __future__ import annotations

import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MCAST_GROUP = "239.255.255.250"
MCAST_PORT = 3702

NS_ENV = "http://www.w3.org/2003/05/soap-envelope"
NS_D = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
NS_A = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
NS_DN = "http://www.onvif.org/ver10/network/wsdl"

TYPE_NVT = f"{NS_DN}/NetworkVideoTransmitter"

_PROBE_TEMPLATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="{NS_ENV}" xmlns:a="{NS_A}" xmlns:d="{NS_D}">
  <s:Header>
    <a:Action>{NS_D}/Probe</a:Action>
    <a:MessageID>urn:uuid:PROBE_UUID</a:MessageID>
    <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
  </s:Header>
  <s:Body>
    <d:Probe>
      <d:Types>{TYPE_NVT}</d:Types>
    </d:Probe>
  </s:Body>
</s:Envelope>"""


@dataclass
class DiscoveredDevice:
    ip: str
    port: int
    service_url: str | None = None
    name: str | None = None
    hardware: str | None = None
    model: str | None = None
    serial: str | None = None
    scopes: list[str] = field(default_factory=list)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(elem: ET.Element | None, local_name: str) -> str | None:
    if elem is None:
        return None
    for child in elem.iter():
        if _local_name(child.tag) == local_name and child.text:
            return child.text.strip()
    return None


def _parse_scope(scope: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not scope.startswith("onvif://"):
        return out
    parts = scope[len("onvif://") :].split("/")
    # Format: onvif://www.onvif.org/<type>/<value> (value may contain slashes)
    if len(parts) >= 3:
        out["type"] = parts[1]
        out["value"] = "/".join(parts[2:])
    return out


def _extract_http_xaddr(xaddrs_text: str | None) -> tuple[str, int, str] | None:
    if not xaddrs_text:
        return None
    for addr in xaddrs_text.split():
        if addr.startswith("http://") or addr.startswith("https://"):
            try:
                parts = urlsplit(addr)
                host = parts.hostname
                port = parts.port or (80 if parts.scheme == "http" else 443)
                if host:
                    return host, port, addr
            except ValueError:
                continue
    return None


def _parse_message(raw: bytes) -> DiscoveredDevice | None:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    is_probe_match = any(
        _local_name(e.tag) == "ProbeMatch" for e in root.iter()
    )

    if not is_probe_match:
        return None

    xaddrs = _first_text(root, "XAddrs")
    addr = _extract_http_xaddr(xaddrs)
    if addr is None:
        return None
    host, port, service_url = addr

    scopes_text = _first_text(root, "Scopes") or ""
    scopes = [s for s in scopes_text.split() if s]

    device = DiscoveredDevice(ip=host, port=port, service_url=service_url, scopes=scopes)

    for scope in scopes:
        parsed = _parse_scope(scope)
        key, value = parsed.get("type"), parsed.get("value")
        if not key or not value:
            continue
        if key == "name":
            device.name = value
        elif key == "hardware":
            device.hardware = value
        elif key == "serial":
            device.serial = value

    return device


def discover(timeout: float = 5.0, bind_ip: str = "0.0.0.0") -> list[DiscoveredDevice]:
    """Broadcast a WS-Discovery Probe and collect ONVIF cameras.

    Blocks for up to `timeout` seconds while responses arrive.
    """
    import uuid as _uuid

    message = _PROBE_TEMPLATE.replace("PROBE_UUID", str(_uuid.uuid4())).encode("utf-8")

    devices: dict[str, DiscoveredDevice] = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind((bind_ip, MCAST_PORT))
        sock.settimeout(0.5)

        sock.sendto(message, (MCAST_GROUP, MCAST_PORT))

        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            device = _parse_message(data)
            if device is not None:
                key = f"{device.ip}:{device.port}"
                devices.setdefault(key, device)
    finally:
        sock.close()

    return list(devices.values())
