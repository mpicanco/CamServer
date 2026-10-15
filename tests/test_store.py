"""Unit tests for the SQLite camera store."""

from __future__ import annotations

from camserver.store import CameraStore


def test_add_list_get_update_delete(tmp_path):
    store = CameraStore(tmp_path / "cameras.db")

    rec = store.add(name="Front Door", ip="192.168.1.10", username="admin")
    assert rec.id
    assert store.get(rec.id).name == "Front Door"

    listed = store.list()
    assert len(listed) == 1
    assert listed[0].ip == "192.168.1.10"

    updated = store.update(rec.id, name="Back Door", rtsp_url="rtsp://x/stream")
    assert updated.name == "Back Door"
    assert updated.rtsp_url == "rtsp://x/stream"

    assert store.get_by_ip("192.168.1.10") is not None
    assert store.get_by_ip("10.0.0.1") is None

    assert store.delete(rec.id) is True
    assert store.get(rec.id) is None
    assert store.delete(rec.id) is False

    store.close()


def test_password_stored_encrypted(tmp_path):
    store = CameraStore(tmp_path / "cameras.db")
    rec = store.add(
        name="Cam", ip="192.168.1.11", password_enc="some-encrypted-token"
    )
    assert store.get(rec.id).password_enc == "some-encrypted-token"
    store.close()


def test_update_ignores_unknown_fields(tmp_path):
    store = CameraStore(tmp_path / "cameras.db")
    rec = store.add(name="Cam", ip="192.168.1.12")
    updated = store.update(rec.id, bogus_field="nope", name="Renamed")
    assert updated.name == "Renamed"
    store.close()
