# CMD Center

Tablet dashboard + control surface for a Linux desktop (Kali), served over the
LAN and used from a kiosk browser (Fully Kiosk on Android).

## What it does

- **Gauge cluster** — live CPU/GPU (usage + thermal gauges), RAM, all mounted
  disks, uptime + running-service count, network, and OS notifications
  (journal warnings), refreshed every 2s.
- **Services tab** — every listening TCP service, with one-tap launch of its
  web dashboard in Chrome on the host.
- **Webcam tab** — WebRTC camera streaming from the tablet to a virtual
  v4l2loopback device on the host.

## Security — READ THIS

**This server has no authentication by design.** It runs on a trusted home LAN
for a personal kiosk. It can:

- run arbitrary whitelisted commands on the host (`/run`)
- reboot, shut down, suspend, and lock the desktop (`/system/*`)
- launch Chrome at arbitrary http(s) URLs (`/open`)

**Do not expose it beyond your LAN.** No port forwarding, no public tunneling,
no running it on an untrusted network. If you need remote access, put it behind
a VPN or add an auth proxy (e.g. oauth2-proxy) in front.

## Run

    cd backend
    python3 -m venv .venv
    .venv/bin/pip install fastapi uvicorn[standard] aioice aiortc av
    .venv/bin/python server.py            # HTTPS (self-signed, auto-generated)
    .venv/bin/python server.py --no-ssl   # plain HTTP

Listens on 0.0.0.0:8081. If launching GUI apps from the dashboard fails, add
`Environment=DISPLAY=:0` and `Environment=XAUTHORITY=/home/<user>/.Xauthority`
to the systemd unit — a user service has no display access by default.

## Layout

    backend/server.py    FastAPI app: status polling, whitelisted actions, WebRTC
    webapp/index.html    single-page dashboard (vanilla JS, kiosk-safe plain links/forms)
    scripts/             setup_v4l2loopback.sh for the webcam feature
    certs/               auto-generated self-signed cert (gitignored)
