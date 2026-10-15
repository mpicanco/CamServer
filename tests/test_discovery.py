"""Unit tests for WS-Discovery parsing."""

from __future__ import annotations

from camserver import discovery

PROBE_MATCH = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
    xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <s:Header>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>
  </s:Header>
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference><a:Address>urn:uuid:abc</a:Address></a:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:Scopes>
          onvif://www.onvif.org/name/FrontDoor
          onvif://www.onvif.org/hardware/IPC-123
          onvif://www.onvif.org/Profile/Streaming
        </d:Scopes>
        <d:XAddrs>http://192.168.1.50:8080/onvif/device_service</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>"""


def test_parse_probe_match():
    device = discovery._parse_message(PROBE_MATCH.encode("utf-8"))
    assert device is not None
    assert device.ip == "192.168.1.50"
    assert device.port == 8080
    assert device.service_url == "http://192.168.1.50:8080/onvif/device_service"
    assert device.name == "FrontDoor"
    assert device.hardware == "IPC-123"


def test_parse_scope_extraction():
    parsed = discovery._parse_scope("onvif://www.onvif.org/name/MyCamera")
    assert parsed == {"type": "name", "value": "MyCamera"}

    parsed = discovery._parse_scope("not-an-onvif-scope")
    assert parsed == {}


def test_extract_http_xaddr():
    result = discovery._extract_http_xaddr(
        "http://10.0.0.5/onvif/device_service onvif://10.0.0.5"
    )
    assert result == ("10.0.0.5", 80, "http://10.0.0.5/onvif/device_service")


def test_ignore_non_probe_messages():
    hello = PROBE_MATCH.replace("ProbeMatches", "Hello").replace(
        "ProbeMatch", "Hello"
    )
    assert discovery._parse_message(hello.encode("utf-8")) is None


def test_parse_garbage_returns_none():
    assert discovery._parse_message(b"not xml at all") is None
