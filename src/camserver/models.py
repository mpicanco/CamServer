"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

import ipaddress

from pydantic import BaseModel, Field, field_validator


class CameraPublic(BaseModel):
    id: str
    name: str
    ip: str
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    onvif_service_url: str | None = None
    rtsp_url: str | None = None
    username: str | None = None
    has_password: bool = False
    enabled: bool = True
    created_at: str
    updated_at: str


class CameraUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    ip: str
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    onvif_service_url: str | None = None
    rtsp_url: str | None = None
    username: str | None = None
    password: str | None = Field(default=None, max_length=256)
    enabled: bool = True

    @field_validator("ip")
    @classmethod
    def _valid_ip(cls, value: str) -> str:
        value = value.strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("ip must be a valid IPv4 or IPv6 address") from None
        return value

    @field_validator("rtsp_url", "onvif_service_url")
    @classmethod
    def _valid_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not (
            value.startswith("rtsp://")
            or value.startswith("http://")
            or value.startswith("https://")
        ):
            raise ValueError("URL must start with rtsp://, http:// or https://")
        return value


class DiscoveredDevice(BaseModel):
    ip: str
    port: int
    service_url: str | None = None
    name: str | None = None
    hardware: str | None = None
    model: str | None = None
    serial: str | None = None
    scopes: list[str] = []
    known: bool = False
    source: str = "onvif"  # "onvif" (WS-Discovery) or "rtsp" (port scan)


class ScanResponse(BaseModel):
    devices: list[DiscoveredDevice]


class TestCameraRequest(BaseModel):
    camera_id: str | None = None
    service_url: str | None = None
    ip: str
    port: int = 80
    username: str | None = None
    password: str | None = None


class TestCameraResponse(BaseModel):
    ok: bool
    rtsp_url: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    error: str | None = None
