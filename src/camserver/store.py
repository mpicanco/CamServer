"""SQLite-backed persistence for cameras."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manufacturer TEXT,
    model TEXT,
    serial TEXT,
    ip TEXT NOT NULL,
    onvif_service_url TEXT,
    rtsp_url TEXT,
    username TEXT,
    password_enc TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cameras_ip ON cameras(ip);
"""


@dataclass
class CameraRecord:
    id: str
    name: str
    ip: str
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    onvif_service_url: str | None = None
    rtsp_url: str | None = None
    username: str | None = None
    password_enc: str | None = None
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class CameraStore:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            db_path.parent.chmod(0o700)
        except OSError:
            pass

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        try:
            db_path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CameraRecord:
        return CameraRecord(
            id=row["id"],
            name=row["name"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            serial=row["serial"],
            ip=row["ip"],
            onvif_service_url=row["onvif_service_url"],
            rtsp_url=row["rtsp_url"],
            username=row["username"],
            password_enc=row["password_enc"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self) -> list[CameraRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM cameras ORDER BY name").fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, camera_id: str) -> CameraRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cameras WHERE id = ?", (camera_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_ip(self, ip: str) -> CameraRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cameras WHERE ip = ?", (ip,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def add(
        self,
        *,
        name: str,
        ip: str,
        manufacturer: str | None = None,
        model: str | None = None,
        serial: str | None = None,
        onvif_service_url: str | None = None,
        rtsp_url: str | None = None,
        username: str | None = None,
        password_enc: str | None = None,
        enabled: bool = True,
    ) -> CameraRecord:
        record = CameraRecord(
            id=uuid.uuid4().hex,
            name=name,
            ip=ip,
            manufacturer=manufacturer,
            model=model,
            serial=serial,
            onvif_service_url=onvif_service_url,
            rtsp_url=rtsp_url,
            username=username,
            password_enc=password_enc,
            enabled=enabled,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cameras (id, name, manufacturer, model, serial, ip,
                    onvif_service_url, rtsp_url, username, password_enc, enabled,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.name,
                    record.manufacturer,
                    record.model,
                    record.serial,
                    record.ip,
                    record.onvif_service_url,
                    record.rtsp_url,
                    record.username,
                    record.password_enc,
                    int(record.enabled),
                    record.created_at,
                    record.updated_at,
                ),
            )
            self._conn.commit()
        return record

    def update(self, camera_id: str, **fields: Any) -> CameraRecord | None:
        record = self.get(camera_id)
        if record is None:
            return None
        allowed = {
            "name",
            "manufacturer",
            "model",
            "serial",
            "ip",
            "onvif_service_url",
            "rtsp_url",
            "username",
            "password_enc",
            "enabled",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "enabled" in updates:
            updates["enabled"] = int(bool(updates["enabled"]))
        updates["updated_at"] = _now()

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [camera_id]
            with self._lock:
                self._conn.execute(
                    f"UPDATE cameras SET {set_clause} WHERE id = ?", values
                )
                self._conn.commit()
        return self.get(camera_id)

    def delete(self, camera_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            self._conn.commit()
        return cur.rowcount > 0
