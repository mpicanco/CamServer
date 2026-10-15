"""Integration tests for the HTTP API (no network/ffmpeg required)."""

from __future__ import annotations

import httpx
import pytest

from camserver.config import Settings
from camserver.main import create_app


@pytest.fixture
async def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as c:
        yield c


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cameras"] == 0


async def test_csrf_required_for_mutations(client):
    payload = {"name": "Cam", "ip": "192.168.1.10", "username": "admin"}

    # No CSRF token -> rejected
    resp = await client.post("/api/cameras", json=payload)
    assert resp.status_code == 403

    # Obtain token (cookie is stored automatically)
    csrf = await client.get("/api/csrf")
    token = csrf.json()["token"]
    assert csrf.cookies.get("camserver_csrf") == token

    # With token -> created
    resp = await client.post(
        "/api/cameras", json=payload, headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["name"] == "Cam"
    assert created["username"] == "admin"
    assert "password" not in created


async def test_password_encrypted_and_not_returned(client):
    csrf = await client.get("/api/csrf")
    token = csrf.json()["token"]
    headers = {"X-CSRF-Token": token}

    resp = await client.post(
        "/api/cameras",
        json={"name": "Cam", "ip": "192.168.1.11", "password": "hunter2"},
        headers=headers,
    )
    body = resp.json()
    assert body["has_password"] is True
    assert "password" not in body

    listed = (await client.get("/api/cameras")).json()
    assert listed[0]["has_password"] is True
    assert all("password" not in c for c in listed)


async def test_update_and_delete(client):
    csrf = await client.get("/api/csrf")
    token = csrf.json()["token"]
    headers = {"X-CSRF-Token": token}

    created = (
        await client.post(
            "/api/cameras",
            json={"name": "Cam", "ip": "192.168.1.12"},
            headers=headers,
        )
    ).json()

    resp = await client.put(
        f"/api/cameras/{created['id']}",
        json={"name": "Renamed", "ip": "192.168.1.12"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"

    resp = await client.delete(f"/api/cameras/{created['id']}", headers=headers)
    assert resp.status_code == 204
    assert (await client.get("/api/cameras")).json() == []


async def test_invalid_ip_rejected(client):
    csrf = await client.get("/api/csrf")
    token = csrf.json()["token"]
    resp = await client.post(
        "/api/cameras",
        json={"name": "Cam", "ip": "not-an-ip"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 422


async def test_host_validation_rejects_unknown_domain(client):
    resp = await client.get("/api/health", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400


async def test_host_validation_allows_ip(client):
    resp = await client.get("/api/health", headers={"Host": "192.168.1.20"})
    assert resp.status_code == 200
