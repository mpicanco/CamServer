# CamServer

A self-hosted web server that discovers IP cameras on your local network via
**ONVIF / WS-Discovery**, streams their **RTSP** video to any web browser
(transcoded to MJPEG with ffmpeg), and runs happily on a **headless Raspberry
Pi**.

Point your browser at `http://<raspberry-pi-ip>:8000` to see all your cameras
live.

## Features

- **Network scan** button: multicast WS-Discovery probe finds ONVIF cameras.
- **Credential prompt**: when a new camera is found, ask for its
  username/password, test the connection, and resolve the RTSP URL via ONVIF
  `GetStreamUri`.
- **Live video**: RTSP is transcoded to MJPEG by ffmpeg and shown with a plain
  `<img>` tag (works in every browser, low CPU).
- **Secure by default** (see [Security](#security)): camera passwords encrypted
  at rest, ffmpeg credentials fed on stdin (not the process command line), CSRF
  protection, DNS-rebinding protection, security headers, rate limiting, and
  strict file permissions.
- **No cloud**: everything stays on your LAN.

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` (used to transcode RTSP to MJPEG)

On a Raspberry Pi (Raspberry Pi OS):

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

## Quick start (development)

```bash
uv sync                       # create venv + install deps
cp .env.example .env          # optional: adjust settings
uv run python -m camserver    # serves on http://0.0.0.0:8000
```

Open `http://localhost:8000`, click **Scan network**, then **Add** any newly
found camera and enter its username/password.

## Deploying on a headless Raspberry Pi

A helper script automates the whole setup (system user, venv, systemd service):

```bash
# on the Pi, after cloning this repo
sudo ./scripts/install-rpi.sh
```

It will:

1. Install `ffmpeg` (if missing).
2. Create a dedicated unprivileged `camserver` system user.
3. Install the project under `/opt/camserver` with `uv`.
4. Install a systemd unit so CamServer starts on boot and restarts on crash.
5. Store all runtime data (SQLite DB + secret key) in
   `/var/lib/camserver` with `0700` permissions.

After installation:

```bash
sudo systemctl enable --now camserver
systemctl status camserver
```

Then browse to `http://<pi-ip>:8000`.

### Manual deployment

```bash
uv sync --locked
sudo useradd --system --home /var/lib/camserver --shell /usr/sbin/nologin camserver
sudo install -d -o camserver -g camserver -m 700 /var/lib/camserver
sudo install -d -o camserver -g camserver /opt/camserver
sudo cp -r . /opt/camserver
sudo chown -R camserver:camserver /opt/camserver
sudo cp scripts/camserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now camserver
```

## Configuration

All settings are read from environment variables (prefix `CAMSERVER_`) or a
`.env` file. See [.env.example](.env.example) for the full list.

Important ones:

| Variable | Default | Description |
| --- | --- | --- |
| `CAMSERVER_HOST` | `0.0.0.0` | Bind address (use a specific interface to restrict exposure). |
| `CAMSERVER_PORT` | `8000` | HTTP port. |
| `CAMSERVER_DATA_DIR` | `data` | Where the DB and secret key live. |
| `CAMSERVER_CREDS_IN_URL` | `false` | Put RTSP credentials in the ffmpeg URL (see Security). |
| `CAMSERVER_ALLOWED_HOSTS` | _(empty)_ | Extra `Host` header values to allow (comma-separated). |

## Security

CamServer is designed for a **trusted LAN** and applies defence-in-depth:

- **No authentication** on the web UI (by design, per the trust-LAN model). If
  you need access control, front it with a reverse proxy (e.g. nginx + basic
  auth) or firewall it to your LAN.
- **Camera passwords encrypted at rest** with Fernet (AES-128-CBC + HMAC). The
  key is stored in `data/secret.key` with `0600` permissions, the data directory
  `0700`.
- **Credentials never in the process list**: by default ffmpeg is launched with
  the RTSP URL *without* credentials and the username/password are written to
  its stdin on the auth challenge. Set `CAMSERVER_CREDS_IN_URL=true` only if a
  camera/ffmpeg combination fails to authenticate over stdin.
- **CSRF protection**: state-changing requests require a double-submit token
  issued in a `SameSite=Strict` cookie.
- **DNS-rebinding protection**: requests are rejected unless the `Host` header
  is an IP literal, `localhost`, `*.local`, or explicitly allowed via
  `CAMSERVER_ALLOWED_HOSTS`.
- **Security headers**: CSP, `X-Frame-Options: DENY`, `nosniff`, restrictive
  `Permissions-Policy`, `Referrer-Policy: no-referrer`.
- **Rate limiting** on the network scan endpoint.
- **Input validation** on all API models (IP addresses, URL schemes, lengths).
- **Secrets are never logged**; ffmpeg runs with `-loglevel warning`.

### Known trade-off

When `CAMSERVER_CREDS_IN_URL=true`, RTSP credentials are visible in the
ffmpeg process arguments to other processes running as the same user
(`ps aux`). This is the standard behaviour of RTSP clients; keep the flag off
for the stronger default.

## How it works

```
Browser ──HTTP──▶ FastAPI/uvicorn ──▶ StreamManager ──▶ ffmpeg ──RTSP──▶ Camera
   │  <img src="/stream/{id}">                                  (ONVIF / RTSP)
   └──── MJPEG multipart/x-mixed-replace ◀──┘
```

1. **Discovery** sends a WS-Discovery `Probe` (UDP multicast to
   `239.255.255.250:3702`) and parses `ProbeMatch` responses.
2. **ONVIF** (`src/camserver/onvif.py`) talks SOAP with WS-Security
   `UsernameToken` (password digest) to `GetDeviceInformation`,
   `GetServices`/`GetProfiles` and `GetStreamUri`, resolving each camera's RTSP
   URL.
3. **Streaming** (`src/camserver/stream.py`) spawns one ffmpeg process per
   active camera, transcodes RTSP to MJPEG frames, and fans frames out to all
   connected browsers.

## Development

```bash
uv run ruff check .        # lint
uv run mypy src            # type check
uv run pytest              # unit tests
```

## Project layout

```
src/camserver/
  main.py        # app factory + entrypoint
  config.py      # pydantic-settings configuration
  crypto.py      # Fernet secret encryption
  store.py       # SQLite persistence
  models.py      # API models
  onvif.py       # minimal ONVIF SOAP client
  discovery.py   # WS-Discovery multicast scanner
  stream.py      # ffmpeg -> MJPEG streaming
  security.py    # CSRF / Host / headers / rate limiting
  api.py         # HTTP routes
  static/        # web frontend (vanilla JS)
```

## License

MIT
