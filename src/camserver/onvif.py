"""Minimal ONVIF client (SOAP + WS-Security UsernameToken) implemented with stdlib + httpx.

Only the operations CamServer needs are implemented: GetDeviceInformation,
GetServices (to locate the media service), GetProfiles and GetStreamUri. This
avoids the heavy (and sometimes Python-version fragile) onvif-zeep dependency.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import os
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import httpx

# ONVIF / SOAP namespaces
NS_ENV = "http://www.w3.org/2003/05/soap-envelope"
NS_WSA = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
NS_WSSE = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
NS_TDS = "http://www.onvif.org/ver10/device/wsdl"
NS_TRT = "http://www.onvif.org/ver10/media/wsdl"
NS_TT = "http://www.onvif.org/ver10/schema"

ACTION_GET_DEVICE_INFORMATION = f"{NS_TDS}/GetDeviceInformation"
ACTION_GET_SERVICES = f"{NS_TDS}/GetServices"
ACTION_GET_PROFILES = f"{NS_TRT}/GetProfiles"
ACTION_GET_STREAM_URI = f"{NS_TRT}/GetStreamUri"

MEDIA_NAMESPACE = f"{NS_TRT}"

DEFAULT_PASSWORD_DIGEST_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)


class OnvifError(Exception):
    """Base error for ONVIF communication failures."""


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _password_digest(nonce: bytes, created: str, password: str) -> str:
    digest = hashlib.sha1(nonce + created.encode("utf-8") + password.encode("utf-8"))
    return base64.b64encode(digest.digest()).decode("ascii")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(elem: ET.Element, local_name: str):
    for child in elem.iter():
        if _local_name(child.tag) == local_name:
            yield child


def _first(elem: ET.Element, local_name: str) -> ET.Element | None:
    return next(_children(elem, local_name), None)


def _text(elem: ET.Element | None) -> str | None:
    return elem.text.strip() if elem is not None and elem.text else None


def _build_envelope(
    service_url: str,
    action: str,
    body_xml: str,
    username: str,
    password: str,
) -> str:
    nonce = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    created = _now_utc()
    digest = _password_digest(nonce, created, password)

    return f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{NS_ENV}" xmlns:a="{NS_WSA}">
  <s:Header>
    <a:Action s:mustUnderstand="1">{action}</a:Action>
    <a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID>
    <a:To s:mustUnderstand="1">{service_url}</a:To>
    <Security s:mustUnderstand="1" xmlns="{NS_WSSE}">
      <UsernameToken>
        <Username>{escape(username)}</Username>
        <Password Type="{DEFAULT_PASSWORD_DIGEST_TYPE}">{digest}</Password>
        <Nonce EncodingType="{NS_WSSE}#Base64Binary">{nonce_b64}</Nonce>
        <Created xmlns="{NS_WSU}">{created}</Created>
      </UsernameToken>
    </Security>
  </s:Header>
  <s:Body>{body_xml}</s:Body>
</s:Envelope>"""


def _request(
    service_url: str,
    action: str,
    body_xml: str,
    username: str,
    password: str,
    timeout: float = 10.0,
) -> ET.Element:
    envelope = _build_envelope(service_url, action, body_xml, username, password)
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    try:
        resp = httpx.post(
            service_url,
            content=envelope.encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise OnvifError(f"connection failed: {exc}") from exc

    if resp.status_code >= 400:
        raise OnvifError(f"SOAP HTTP {resp.status_code}")

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise OnvifError("invalid SOAP response") from exc

    # Surface SOAP faults with a readable message.
    reason = _text(_first(root, "Reason"))
    if reason is not None:
        raise OnvifError(f"SOAP fault: {reason}")
    return root


def get_device_information(
    service_url: str, username: str, password: str, timeout: float = 10.0
) -> dict[str, str | None]:
    body = f'<tds:GetDeviceInformation xmlns:tds="{NS_TDS}"/>'
    root = _request(service_url, ACTION_GET_DEVICE_INFORMATION, body, username, password, timeout)
    return {
        "manufacturer": _text(_first(root, "Manufacturer")),
        "model": _text(_first(root, "Model")),
        "serial": _text(_first(root, "SerialNumber")),
    }


def _find_media_xaddr(service_url: str, root: ET.Element) -> str | None:
    for service in _children(root, "Service"):
        namespace = _text(_first(service, "Namespace"))
        if namespace and "media/wsdl" in namespace:
            xaddr = _text(_first(service, "XAddr"))
            if xaddr:
                return xaddr
    # Fallback: scan all XAddrs for one that mentions "media"
    for xaddr in _children(root, "XAddr"):
        value = xaddr.text
        if value and "media" in value:
            return value.strip()
    return None


def get_media_service_url(
    device_service_url: str, username: str, password: str, timeout: float = 10.0
) -> str:
    # Preferred: GetServices (widely supported, authoritative).
    body = (
        f'<tds:GetServices xmlns:tds="{NS_TDS}">'
        "<tds:IncludeCapability>false</tds:IncludeCapability></tds:GetServices>"
    )
    try:
        root = _request(device_service_url, ACTION_GET_SERVICES, body, username, password, timeout)
        xaddr = _find_media_xaddr(device_service_url, root)
        if xaddr:
            return xaddr
    except OnvifError:
        pass

    # Fallback: conventional path substitution.
    for device_suffix, media_suffix in (
        ("/onvif/device_service", "/onvif/media_service"),
        ("/onvif/device_service/", "/onvif/media_service/"),
    ):
        if device_service_url.endswith(device_suffix):
            return device_service_url[: -len(device_suffix)] + media_suffix

    raise OnvifError("could not locate ONVIF media service URL")


def get_profiles(
    media_service_url: str, username: str, password: str, timeout: float = 10.0
) -> list[dict[str, str]]:
    body = f'<trt:GetProfiles xmlns:trt="{NS_TRT}"/>'
    root = _request(media_service_url, ACTION_GET_PROFILES, body, username, password, timeout)
    profiles: list[dict[str, str]] = []
    for profile in _children(root, "Profiles"):
        token = profile.get("token")
        if token:
            profiles.append(
                {"token": token, "name": _text(_first(profile, "Name")) or ""}
            )
    return profiles


def get_stream_uri(
    media_service_url: str,
    profile_token: str,
    username: str,
    password: str,
    timeout: float = 10.0,
) -> str:
    body = f"""<trt:GetStreamUri xmlns:trt="{NS_TRT}" xmlns:tt="{NS_TT}">
      <trt:StreamSetup>
        <tt:Stream>RTP-Unicast</tt:Stream>
        <tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
      </trt:StreamSetup>
      <trt:ProfileToken>{escape(profile_token)}</trt:ProfileToken>
    </trt:GetStreamUri>"""
    root = _request(media_service_url, ACTION_GET_STREAM_URI, body, username, password, timeout)
    uri = _text(_first(root, "Uri"))
    if not uri:
        raise OnvifError("GetStreamUri returned no URI")
    return uri


def test_camera(
    service_url: str, username: str, password: str, timeout: float = 10.0
) -> dict[str, str | None]:
    """Run the full ONVIF probe used by the "test connection" flow."""
    info = get_device_information(service_url, username, password, timeout)
    media_url = get_media_service_url(service_url, username, password, timeout)
    profiles = get_profiles(media_url, username, password, timeout)
    if not profiles:
        raise OnvifError("camera has no media profiles")
    # Prefer a profile whose name hints at a main stream.
    profile = next((p for p in profiles if "main" in p["name"].lower()), profiles[0])
    rtsp_url = get_stream_uri(media_url, profile["token"], username, password, timeout)
    return {
        "rtsp_url": rtsp_url,
        "manufacturer": info["manufacturer"],
        "model": info["model"],
        "serial": info["serial"],
    }
