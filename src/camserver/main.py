"""FastAPI application factory and entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router as api_router
from .config import Settings, get_settings
from .crypto import SecretBox
from .security import (
    CSRFMiddleware,
    HostValidationMiddleware,
    RateLimiter,
    SecurityHeadersMiddleware,
)
from .store import CameraStore
from .stream import StreamManager

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    store = CameraStore(settings.db_path)
    secret_box = SecretBox(settings.secret_key_path)
    streams = StreamManager(settings, secret_box)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await streams.close_all()
        store.close()

    app = FastAPI(
        title="CamServer",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.store = store
    app.state.secret_box = secret_box
    app.state.streams = streams
    app.state.scan_limiter = RateLimiter(1, settings.scan_rate_limit)

    # Middleware order: last added = outermost.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(HostValidationMiddleware, allowed_hosts=settings.allowed_hosts_list)
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(api_router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
