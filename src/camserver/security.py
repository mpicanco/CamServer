"""Security middleware: security headers, DNS-rebinding (Host) validation,
CSRF double-submit protection and a simple in-memory rate limiter."""

from __future__ import annotations

import ipaddress
import secrets
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

CSRF_COOKIE = "camserver_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _host_allowed(host: str, extra_allowed: list[str]) -> bool:
    """Return True if `host` (no port) is an IP literal, localhost, *.local,
    or listed in `extra_allowed`."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass

    host_lower = host.lower()
    if host_lower == "localhost":
        return True
    if host_lower.endswith(".local"):
        return True
    return host_lower in extra_allowed


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; "
            "style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return response


class HostValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_hosts: list[str]) -> None:
        super().__init__(app)
        self._allowed = [h.lower() for h in allowed_hosts]

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        hostname = host.rsplit(":", 1)[0] if host.count(":") <= 1 else host
        if not hostname or not _host_allowed(hostname, self._allowed):
            return JSONResponse({"detail": "invalid host header"}, status_code=400)
        return await call_next(request)


class CSRFMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def issue_token() -> str:
        return secrets.token_urlsafe(32)

    async def dispatch(self, request: Request, call_next):
        if request.method in CSRF_SAFE_METHODS:
            return await call_next(request)
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get(CSRF_HEADER)
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            return JSONResponse(
                {"detail": "missing or invalid CSRF token"}, status_code=403
            )
        return await call_next(request)


class RateLimiter:
    """Fixed-window-ish in-memory limiter keyed by client IP."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._hits[key]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.max_requests:
            return False
        dq.append(now)
        return True
