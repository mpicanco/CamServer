"""HTTP API routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__, discovery, onvif
from .models import (
    CameraPublic,
    CameraUpsert,
    DiscoveredDevice,
    ScanResponse,
    TestCameraRequest,
    TestCameraResponse,
)
from .security import CSRF_COOKIE, CSRFMiddleware, RateLimiter
from .store import CameraRecord
from .stream import StreamError, StreamManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _to_public(record: CameraRecord) -> CameraPublic:
    return CameraPublic(
        id=record.id,
        name=record.name,
        ip=record.ip,
        manufacturer=record.manufacturer,
        model=record.model,
        serial=record.serial,
        onvif_service_url=record.onvif_service_url,
        rtsp_url=record.rtsp_url,
        username=record.username,
        has_password=bool(record.password_enc),
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _get_camera_or_404(request: Request, camera_id: str) -> CameraRecord:
    camera = request.app.state.store.get(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return camera


@router.get("/health")
async def health(request: Request) -> dict:
    cameras = request.app.state.store.list()
    return {
        "status": "ok",
        "version": __version__,
        "cameras": len(cameras),
        "streams": len(request.app.state.streams._streams),
    }


@router.get("/csrf")
async def csrf(request: Request) -> JSONResponse:
    token = request.cookies.get(CSRF_COOKIE) or CSRFMiddleware.issue_token()
    response = JSONResponse({"token": token})
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        secure=False,
    )
    return response


@router.get("/cameras", response_model=list[CameraPublic])
async def list_cameras(request: Request) -> list[CameraPublic]:
    return [_to_public(c) for c in request.app.state.store.list()]


@router.post("/cameras", response_model=CameraPublic, status_code=201)
async def create_camera(request: Request, payload: CameraUpsert) -> CameraPublic:
    store = request.app.state.store
    secret_box = request.app.state.secret_box
    password_enc = secret_box.encrypt(payload.password) if payload.password else None
    record = store.add(
        name=payload.name,
        ip=payload.ip,
        manufacturer=payload.manufacturer,
        model=payload.model,
        serial=payload.serial,
        onvif_service_url=payload.onvif_service_url,
        rtsp_url=payload.rtsp_url,
        username=payload.username,
        password_enc=password_enc,
        enabled=payload.enabled,
    )
    return _to_public(record)


@router.put("/cameras/{camera_id}", response_model=CameraPublic)
async def update_camera(
    request: Request, camera_id: str, payload: CameraUpsert
) -> CameraPublic:
    store = request.app.state.store
    secret_box = request.app.state.secret_box
    _get_camera_or_404(request, camera_id)

    fields: dict = {
        "name": payload.name,
        "ip": payload.ip,
        "manufacturer": payload.manufacturer,
        "model": payload.model,
        "serial": payload.serial,
        "onvif_service_url": payload.onvif_service_url,
        "rtsp_url": payload.rtsp_url,
        "username": payload.username,
        "enabled": payload.enabled,
    }
    if payload.password:
        fields["password_enc"] = secret_box.encrypt(payload.password)

    record = store.update(camera_id, **fields)
    assert record is not None
    return _to_public(record)


@router.delete("/cameras/{camera_id}", status_code=204)
async def delete_camera(request: Request, camera_id: str) -> None:
    store = request.app.state.store
    camera = _get_camera_or_404(request, camera_id)
    await request.app.state.streams.stop_stream(camera.id)
    if not store.delete(camera.id):
        raise HTTPException(status_code=404, detail="camera not found")


@router.post("/scan", response_model=ScanResponse)
async def scan_network(request: Request) -> ScanResponse:
    settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.scan_limiter
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="scan rate limit exceeded")

    known_ips = {c.ip for c in request.app.state.store.list()}
    try:
        found = await asyncio.to_thread(
            discovery.discover, settings.discovery_timeout
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"discovery failed: {exc}") from exc

    devices = [
        DiscoveredDevice(
            ip=d.ip,
            port=d.port,
            service_url=d.service_url,
            name=d.name,
            hardware=d.hardware,
            model=d.model,
            serial=d.serial,
            scopes=d.scopes,
            known=d.ip in known_ips,
        )
        for d in found
    ]
    devices.sort(key=lambda d: d.ip)
    return ScanResponse(devices=devices)


@router.post("/cameras/test", response_model=TestCameraResponse)
async def test_camera(request: Request, payload: TestCameraRequest) -> TestCameraResponse:
    if not payload.username or not payload.password:
        raise HTTPException(status_code=422, detail="username and password are required")

    service_url = payload.service_url
    if not service_url:
        service_url = f"http://{payload.ip}:{payload.port}/onvif/device_service"

    try:
        result = await asyncio.to_thread(
            onvif.test_camera, service_url, payload.username, payload.password
        )
    except onvif.OnvifError as exc:
        return TestCameraResponse(ok=False, error=str(exc))
    except Exception:  # noqa: BLE001
        logger.exception("unexpected ONVIF test error")
        return TestCameraResponse(ok=False, error="unexpected error")

    return TestCameraResponse(
        ok=True,
        rtsp_url=result["rtsp_url"],
        manufacturer=result["manufacturer"],
        model=result["model"],
        serial=result["serial"],
    )


@router.get("/stream/{camera_id}")
async def stream_mjpeg(request: Request, camera_id: str) -> StreamingResponse:
    camera = _get_camera_or_404(request, camera_id)
    if not camera.enabled:
        raise HTTPException(status_code=409, detail="camera is disabled")

    streams: StreamManager = request.app.state.streams
    try:
        queue, _ = await streams.subscribe(camera)
    except StreamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(
            status_code=502, detail="ffmpeg not found on server"
        ) from None

    async def frames():
        try:
            yield b"--frame\r\n"
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield b"--frame\r\nContent-Type: text/plain\r\n\r\n\r\n"
                    continue
                if frame == b"":
                    break
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode("ascii")
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
        finally:
            streams.unsubscribe(camera.id, queue)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
