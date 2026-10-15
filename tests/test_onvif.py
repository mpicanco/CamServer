"""Unit tests for the ONVIF client (SOAP building/parsing, password digest)."""

from __future__ import annotations

import base64
import hashlib
import xml.etree.ElementTree as ET

import httpx

from camserver import onvif


def test_password_digest_matches_wsse_spec():
    nonce = b"\x01\x02\x03\x04" * 4
    created = "2026-01-01T00:00:00Z"
    password = "secret"
    digest = onvif._password_digest(nonce, created, password)
    expected = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    assert digest == expected


def test_build_envelope_contains_security_token():
    env = onvif._build_envelope(
        "http://cam/onvif/device_service",
        onvif.ACTION_GET_DEVICE_INFORMATION,
        "<x/>",
        "admin",
        "pw",
    )
    assert "UsernameToken" in env
    assert "<Username>admin</Username>" in env
    assert "PasswordDigest" in env
    assert "Password" not in env or "Password>" in env


def test_parse_get_stream_uri():
    xml = f"""<s:Envelope xmlns:s="{onvif.NS_ENV}" xmlns:tt="{onvif.NS_TT}"
        xmlns:trt="{onvif.NS_TRT}">
      <s:Body>
        <trt:GetStreamUriResponse>
          <trt:MediaUri>
            <tt:Uri>rtsp://192.168.1.50:554/Streaming/Channels/101</tt:Uri>
            <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
          </trt:MediaUri>
        </trt:GetStreamUriResponse>
      </s:Body>
    </s:Envelope>"""
    root = ET.fromstring(xml)
    assert (
        onvif._text(onvif._first(root, "Uri"))
        == "rtsp://192.168.1.50:554/Streaming/Channels/101"
    )


def test_parse_get_profiles():
    xml = f"""<s:Envelope xmlns:s="{onvif.NS_ENV}" xmlns:trt="{onvif.NS_TRT}">
      <s:Body>
        <trt:GetProfilesResponse>
          <trt:Profiles token="Profile_1" fixed="true">
            <trt:Name>MainStream</trt:Name>
          </trt:Profiles>
        </trt:GetProfilesResponse>
      </s:Body>
    </s:Envelope>"""
    root = ET.fromstring(xml)
    profiles = [
        {"token": p.get("token"), "name": onvif._text(onvif._first(p, "Name")) or ""}
        for p in onvif._children(root, "Profiles")
        if p.get("token")
    ]
    assert profiles == [{"token": "Profile_1", "name": "MainStream"}]


def test_find_media_xaddr_from_get_services():
    xml = f"""<s:Envelope xmlns:s="{onvif.NS_ENV}" xmlns:tds="{onvif.NS_TDS}">
      <s:Body>
        <tds:GetServicesResponse>
          <tds:Service>
            <tds:Namespace>{onvif.MEDIA_NAMESPACE}</tds:Namespace>
            <tds:XAddr>http://192.168.1.50/onvif/media_service</tds:XAddr>
            <tds:Version><tds:Major>2</tds:Major><tds:Minor>6</tds:Minor></tds:Version>
          </tds:Service>
        </tds:GetServicesResponse>
      </s:Body>
    </s:Envelope>"""
    root = ET.fromstring(xml)
    assert (
        onvif._find_media_xaddr("http://x", root)
        == "http://192.168.1.50/onvif/media_service"
    )


def test_get_stream_uri_http_error(monkeypatch):
    def fake_post(url, **kwargs):
        return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(onvif.httpx, "post", fake_post)
    import pytest

    with pytest.raises(onvif.OnvifError):
        onvif.get_stream_uri(
            "http://cam/onvif/media_service", "Profile_1", "u", "p"
        )
