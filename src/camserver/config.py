"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAMSERVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Path("data")

    ffmpeg_binary: str = "ffmpeg"

    # Discovery
    discovery_timeout: float = 5.0

    # Stream transcoding (RTSP -> MJPEG)
    stream_fps: int = 15
    stream_width: int = 1280
    stream_quality: int = 5
    rtsp_transport: str = "tcp"
    creds_in_url: bool = False

    # DNS-rebinding protection: extra allowed Host header values (comma separated)
    allowed_hosts: str = ""

    scan_rate_limit: float = 30.0

    # RTSP port scan (pure-RTSP cameras have no discovery protocol)
    scan_subnets: str = ""  # comma-separated CIDRs; empty = auto-detect local subnets
    scan_ports: str = "554,8554"
    scan_port_timeout: float = 1.0

    log_level: str = "info"

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def scan_subnets_list(self) -> list[str]:
        return [s.strip() for s in self.scan_subnets.split(",") if s.strip()]

    @property
    def scan_ports_tuple(self) -> tuple[int, ...]:
        ports = [int(p) for p in self.scan_ports.split(",") if p.strip().isdigit()]
        return tuple(ports) if ports else (554,)

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cameras.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
