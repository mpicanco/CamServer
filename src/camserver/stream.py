"""ffmpeg-backed RTSP -> MJPEG streaming with per-camera fan-out to browsers."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote, urlsplit, urlunsplit

from .config import Settings
from .store import CameraRecord

logger = logging.getLogger(__name__)

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class StreamError(Exception):
    pass


# Common RTSP path suffixes used to probe a camera whose ONVIF interface is
# unavailable. Ordered roughly by popularity so the common case is found early.
RTSP_PROBE_PATHS = (
    "",
    "Streaming/Channels/101",
    "Streaming/Channels/1",
    "stream1",
    "11",
    "live",
    "h264/ch1/main/av_stream",
    "cam/realmonitor?channel=1&subtype=0",
    "h264",
    "media/video1",
    "av0_0",
    "unicast",
)


def _probe_urls(ip: str, port: int, username: str, password: str) -> list[str]:
    host = f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    base = f"rtsp://{userinfo}@{host}:{port}"
    return [base + (f"/{p}" if p else "") for p in RTSP_PROBE_PATHS]


async def _rtsp_url_works(
    ffmpeg_binary: str, url: str, rtsp_transport: str, timeout: float
) -> bool:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        rtsp_transport,
        "-stimeout",
        str(int(timeout * 1_000_000)),
        "-i",
        url,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        code = await asyncio.wait_for(proc.wait(), timeout=timeout + 5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return code == 0


async def probe_rtsp_url(
    settings: Settings,
    ip: str,
    port: int,
    username: str,
    password: str,
    timeout: float = 4.0,
    concurrency: int = 3,
) -> str | None:
    """Return the first RTSP URL (from typical formats) that ffmpeg can read."""
    urls = _probe_urls(ip, port, username, password)
    sem = asyncio.Semaphore(concurrency)

    async def check(url: str) -> str | None:
        async with sem:
            if await _rtsp_url_works(
                settings.ffmpeg_binary, url, settings.rtsp_transport, timeout
            ):
                return url
        return None

    tasks = [asyncio.create_task(check(u)) for u in urls]
    try:
        for coro in asyncio.as_completed(tasks):
            url = await coro
            if url:
                return url
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    return None


def rtsp_url_with_auth(rtsp_url: str, username: str, password: str) -> str:
    """Inject username/password into an rtsp:// URL (used when creds_in_url is set)."""
    parts = urlsplit(rtsp_url)
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    netloc = f"{userinfo}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class _CameraStream:
    def __init__(self, manager: StreamManager, camera: CameraRecord) -> None:
        self.manager = manager
        self.camera = camera
        self.subscribers: set[asyncio.Queue[bytes]] = set()
        self.proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._buffer = b""
        self._closed = False

    def _ffmpeg_args(self, url: str) -> list[str]:
        s = self.manager.settings
        return [
            s.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            s.rtsp_transport,
            "-stimeout",
            "10000000",
            "-i",
            url,
            "-vf",
            f"scale={s.stream_width}:-2",
            "-r",
            str(s.stream_fps),
            "-c:v",
            "mjpeg",
            "-q:v",
            str(s.stream_quality),
            "-an",
            "-f",
            "image2pipe",
            "pipe:1",
        ]

    async def start(self) -> None:
        s = self.manager.settings
        camera = self.camera
        rtsp_url = camera.rtsp_url
        if not rtsp_url:
            raise StreamError("camera has no RTSP URL")

        url = rtsp_url
        feed_stdin = False
        if camera.username and not s.creds_in_url:
            feed_stdin = True
        elif camera.username and s.creds_in_url:
            url = rtsp_url_with_auth(rtsp_url, camera.username, self._password())

        self.proc = await asyncio.create_subprocess_exec(
            *self._ffmpeg_args(url),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if feed_stdin:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(
                    f"{camera.username}\n{self._password()}\n".encode()
                )
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                logger.warning("ffmpeg closed stdin early for camera %s", camera.id)

        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    def _password(self) -> str:
        if self.camera.password_enc is None:
            return ""
        try:
            return self.manager.secret_box.decrypt(self.camera.password_enc)
        except Exception:
            logger.warning("could not decrypt password for camera %s", self.camera.id)
            return ""

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while not self._closed:
            try:
                chunk = await self.proc.stdout.read(65536)
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                break
            self._buffer += chunk
            while True:
                soi = self._buffer.find(JPEG_SOI)
                if soi < 0:
                    self._buffer = self._buffer[-2:]
                    break
                eoi = self._buffer.find(JPEG_EOI, soi + 2)
                if eoi < 0:
                    if len(self._buffer) > 4_000_000:  # runaway guard
                        self._buffer = self._buffer[soi:]
                    break
                frame = self._buffer[soi : eoi + 2]
                self._buffer = self._buffer[eoi + 2 :]
                await self._broadcast(frame)
        await self._notify_end()

    async def _stderr_loop(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            try:
                line = await self.proc.stderr.readline()
            except Exception:  # noqa: BLE001
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logger.debug("ffmpeg[%s]: %s", self.camera.id, text)

    async def _broadcast(self, frame: bytes) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass  # drop frames for slow clients (live stream)

    async def _notify_end(self) -> None:
        self._closed = True
        for q in list(self.subscribers):
            try:
                q.put_nowait(b"")  # empty frame signals end-of-stream
            except asyncio.QueueFull:
                pass
        self.manager._on_stream_ended(self)

    async def stop(self) -> None:
        self._closed = True
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass


class StreamManager:
    def __init__(self, settings: Settings, secret_box) -> None:
        self.settings = settings
        self.secret_box = secret_box
        self._streams: dict[str, _CameraStream] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, camera: CameraRecord) -> tuple[asyncio.Queue[bytes], bool]:
        """Return a frame queue for `camera` and whether the stream started fresh."""
        async with self._lock:
            stream = self._streams.get(camera.id)
            fresh = False
            if stream is None or stream._closed:
                stream = _CameraStream(self, camera)
                self._streams[camera.id] = stream
                await stream.start()
                fresh = True
            queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
            stream.subscribers.add(queue)
            return queue, fresh

    def unsubscribe(self, camera_id: str, queue: asyncio.Queue[bytes]) -> None:
        stream = self._streams.get(camera_id)
        if stream is None:
            return
        stream.subscribers.discard(queue)
        if not stream.subscribers:
            self._streams.pop(camera_id, None)
            asyncio.create_task(stream.stop())

    def _on_stream_ended(self, stream: _CameraStream) -> None:
        self._streams.pop(stream.camera.id, None)

    async def stop_stream(self, camera_id: str) -> None:
        stream = self._streams.pop(camera_id, None)
        if stream is not None:
            await stream.stop()

    async def close_all(self) -> None:
        for stream in list(self._streams.values()):
            await stream.stop()
        self._streams.clear()
