# CMD Center

A "car dashboard" for your Linux desktop: a tablet-mounted gauge cluster and
stream-deck-style control surface, served over your LAN and used from a kiosk
browser (built against [Fully Kiosk](https://www.fully-kiosk.com/) on Android,
works in any modern browser).

One page tells you everything about the machine underneath, and a row of big
touch targets controls it from across the room.

![tabs](https://img.shields.io/badge/tabs-Dashboard%20%C2%B7%20Services%20%C2%B7%20Webcam-88c0d0)
![theme](https://img.shields.io/badge/theme-Nord-2e3440)

## Features

### Gauge cluster (Dashboard)

Live system state, refreshed every 2 seconds:

- **CPU / GPU** — usage bar plus a dedicated thermal gauge (green → amber → red
  as temps climb)
- **RAM** — usage with total
- **Disks** — every real mounted filesystem with its own mini bar (root first)
- **State** — uptime and running systemd services (system + user)
- **Network** — interface and address
- **Notifications** — recent OS journal warnings/errors, newest last
- **Top processes** — sorted by CPU
- **Workspaces** — one-tap compositor workspace switching
- **Quick power** — lock, sleep, reboot, shutdown

Live clock in the header; Nord color scheme.

### Services tab

Every listening TCP service on the host, resolved to human-readable names
(systemd units and a known-process map) with its port and bind address.
Services with a known web UI get a one-tap **Open** button that launches
Chrome on the host pointed at that service's dashboard.

### Webcam tab

Turns an Android tablet into a wireless webcam for the host. The tablet's
camera is captured in the browser and streamed to a
[v4l2loopback](https://github.com/umlaeute/v4l2loopback) virtual video device
on the host (`/dev/video10` by default), so OBS, Zoom, Meet, or anything else
sees it as a regular webcam.

Three transport modes, selectable in the UI:

| Mode | Latency | Notes |
|---|---|---|
| WebRTC | ~40ms | Hardware H.264 via aiortc. Recommended. |
| MJPEG | ~50–150ms | Direct JPEG frames over WebSocket. Simple fallback. |
| WebM | higher | Compressed stream fallback for restrictive networks. |

Includes front/back camera selection, resolution presets (480/720/1080),
mirroring, auto-start on load, and live streaming health counters
(`GET /cam/status` → frames in/written/dropped, measured FPS).

### Lock mirroring (Fully Kiosk)

With Fully Kiosk's Remote Admin enabled, the host watches its session lock
state and mirrors it onto the tablet: **machine locks → tablet screen off,
machine unlocks → tablet screen on.** Uses omarchy's session-lock probe
(Hyprland), but the mechanism is trivially adaptable to other compositors.

## Security — READ THIS

**This server has no authentication by design.** It is built for a personal
kiosk on a trusted home LAN. The API can:

- run whitelisted commands on the host (`/run`, `/command/run`) — the
  allowlist covers terminals and system tools, and a `sudo` command in a
  terminal still needs your password
- reboot, shut down, suspend, and lock the desktop (`/system/*`)
- launch Chrome at arbitrary http(s) URLs (`/open`)
- kill processes holding the virtual camera device (`/webrtc/offer`)

**Do not expose it beyond your LAN.** No port forwarding, no public tunneling,
no running it on an untrusted network. If you need remote access, put it
behind a VPN (e.g. WireGuard/Tailscale) or an auth proxy (e.g. oauth2-proxy).

Secrets: the Fully Kiosk remote-admin password (if you use lock mirroring)
lives in `backend/fully-psswd.txt`, which is gitignored, or in a `FULLY_PASS`
environment variable. It is never hardcoded and never committed.

## Requirements

- Linux (developed on Arch/Omarchy; Debian works — the setup script is
  distro-aware)
- Python 3.11+
- A LAN with a tablet or any device running a modern browser
- For the webcam feature: `v4l2loopback` (DKMS), `ffmpeg`
- For lock mirroring (optional): [Fully Kiosk](https://www.fully-kiosk.com/)
  Browser on the tablet with Remote Admin enabled, and omarchy-style session
  lock tooling

## Setup

### 1. Backend

    cd backend
    python3 -m venv .venv
    .venv/bin/pip install fastapi "uvicorn[standard]" aioice aiortc av

    .venv/bin/python server.py            # HTTPS (self-signed, auto-generated)
    .venv/bin/python server.py --no-ssl   # plain HTTP

Listens on `0.0.0.0:8081`. Open `https://<host-ip>:8081` on the tablet and
accept the self-signed certificate warning.

**Running as a systemd user service** (recommended for a kiosk) — a user
service has no display access by default, so GUI-launch features need:

    # ~/.config/systemd/user/cmd-center.service
    [Service]
    Environment=DISPLAY=:0
    Environment=XAUTHORITY=/home/<user>/.Xauthority

Then `systemctl --user daemon-reload && systemctl --user enable --now cmd-center`.

### 2. Virtual webcam

    sudo scripts/setup_v4l2loopback.sh

Distro-aware (apt/pacman), creates `/dev/video10` with `exclusive_caps=1`
(so browsers and conferencing apps recognize it), uses `max_buffers=8` to
avoid stalls under bursty load, and writes the persistence configs so it
survives reboots.

### 3. Lock mirroring (optional)

1. In Fully Kiosk: Settings → Remote Admin → enable, set a password
2. Put the password in `backend/fully-psswd.txt` (or `FULLY_PASS` env var)
   and set the tablet address via `FULLY_URL` if different from the default

The backend polls the compositor lock state every 2s and sends
`screenOff`/`screenOn` to the tablet only on state changes.

## Layout

    backend/server.py    FastAPI app: status polling, whitelisted actions,
                         WebRTC/WS webcam ingest, lock mirror
    backend/fully-psswd.txt  Fully Kiosk remote-admin password (gitignored)
    webapp/index.html    single-page dashboard (vanilla JS; actions are plain
                         links/forms so they work even if JS is flaky)
    scripts/setup_v4l2loopback.sh  virtual webcam setup (distro-aware)
    certs/               auto-generated self-signed cert (gitignored)

## API overview

| Endpoint | What it does |
|---|---|
| `GET /system/status` | Full system state (CPU, RAM, disks, GPU, temps, services, notifications, processes) |
| `GET /system/{lock,sleep,reboot,shutdown}` | Power/session actions |
| `GET /launch?app=` | Launch a whitelisted GUI app |
| `GET /run?command=&terminal=1` | Run an allowlisted command, optionally in a terminal |
| `GET /open?url=` | Open a URL in Chrome on the host |
| `GET /services/ports` | All listening TCP services (name, port, address, web-UI URL) |
| `GET /cam/status` | Webcam stream health (frames in/written/dropped, measured FPS) |
| `WS /ws/cam` | Tablet camera ingest (MJPEG or WebM) |
| `POST /webrtc/offer` | WebRTC negotiation for the low-latency path |

## License

MIT
