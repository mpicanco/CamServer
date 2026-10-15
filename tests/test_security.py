"""Unit tests for security helpers."""

from __future__ import annotations

from camserver.security import RateLimiter, _host_allowed


def test_host_allowed_ip_literals():
    assert _host_allowed("192.168.1.10", []) is True
    assert _host_allowed("::1", []) is True
    assert _host_allowed("localhost", []) is True


def test_host_allowed_dot_local():
    assert _host_allowed("raspberrypi.local", []) is True


def test_host_allowed_extra():
    assert _host_allowed("cams.home", ["cams.home"]) is True
    assert _host_allowed("evil.com", []) is False
    assert _host_allowed("attacker.com", ["cams.home"]) is False


def test_rate_limiter():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    assert limiter.allow("5.6.7.8") is True
