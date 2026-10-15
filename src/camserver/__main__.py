"""Run CamServer with uvicorn: `python -m camserver`."""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .main import create_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
