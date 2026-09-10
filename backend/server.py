"""CMD Center backend — gauge cluster + stream deck for the tablet kiosk.

All action endpoints return a small HTML redirect page (kiosk-safe, no JSON
in the browser). State endpoints return JSON for the dashboard's JS polling.
"""
import argparse
import asyncio
import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

APP_DIR = Path(__file__).resolve().parent.parent
WEBAPP = APP_DIR / "webapp" / "index.html"
CAPTURE_DIR = APP_DIR / "captures"
CAPTURE_DIR.mkdir(exist_ok=True)
CERTS_DIR = APP_DIR / "certs"
CERT_FILE = CERTS_DIR / "cert.pem"
KEY_FILE = CERTS_DIR / "key.pem"

CAM_DEVICE = os.environ.get("CAM_DEVICE", "/dev/video10")

TERMINAL = "x-terminal-emulator"  # qterminal on this machine

# Apps launchable by name (verified installed on this host)
APPS = {
    "firefox": "firefox",
    "files": "thunar",
    "terminal": TERMINAL,
    "editor": "mousepad",
    "vlc": "vlc",
    "steam": "steam",
    "taskmanager": "xfce4-taskmanager",
    "bluetooth": "blueman-manager",
    "network": "nm-connection-editor",
    "settings": "xfce4-settings-manager",
}

# Command prefixes allowed through /run and /command/run
CMD_OK = re.compile(
    r"^(x-terminal-emulator|qterminal|xterm|wmctrl|playerctl|pactl|systemctl|"
    r"loginctl|reboot|shutdown|docker|journalctl|df|ss|nmcli|ping|ncdu|htop|"
    r"btop|free|uptime|sensors|nvidia-smi|steam|vlc|firefox|thunar|mousepad|"
    r"xfce4-taskmanager|xfce4-screenshooter|recordmydesktop|speedtest|curl)"
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _redirect_page(msg: str = "ok") -> HTMLResponse:
    return HTMLResponse(
        f"<html><body style='background:#05080e;color:#00e5ff;"
        f"font-family:monospace'>{msg}"
        "<script>setTimeout(()=>location.href='/',700);</script></body></html>"
    )


def _spawn(cmd: str):
    """Fire-and-forget launch on the host display."""
    subprocess.Popen(cmd, shell=True, start_new_session=True)


def _run_terminal(cmd: str):
    """Run cmd inside a new terminal window that stays open afterwards."""
    quoted = shlex.quote(cmd + "; exec bash")
    _spawn(f"{TERMINAL} -e bash -lc {quoted}")


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=WEBAPP.read_text())


@app.get("/config")
async def config():
    return JSONResponse({"workspaces": _workspaces()})


# ------------------------------------------------------- actions (GET)
@app.get("/launch")
async def launch_get(app: str = ""):
    if app not in APPS:
        return _redirect_page("unknown app")
    _spawn(APPS[app])
    return _redirect_page("launched " + app)


@app.get("/run")
async def run_get(command: str = "", terminal: int = 0):
    if not CMD_OK.match(command):
        return _redirect_page("command not allowed")
    if terminal:
        _run_terminal(command)
    else:
        _spawn(command)
    return _redirect_page("running")


@app.post("/command/run")
async def command_run_post(command: str = "", terminal: int = 0):
    if not CMD_OK.match(command):
        return _redirect_page("command not allowed")
    if terminal:
        _run_terminal(command)
    else:
        _spawn(command)
    return _redirect_page("running")


@app.get("/media/{action}")
async def media_get(action: str, v: int = 50):
    cmds = {
        "play": "playerctl play",
        "pause": "playerctl pause",
        "prev": "playerctl previous",
        "next": "playerctl next",
        "mute": "pactl set-sink-mute @DEFAULT_SINK@ 1",
        "unmute": "pactl set-sink-mute @DEFAULT_SINK@ 0",
        "volup": "pactl set-sink-volume @DEFAULT_SINK@ +10%",
        "voldown": "pactl set-sink-volume @DEFAULT_SINK@ -10%",
    }
    if action == "volume":
        _spawn(f"pactl set-sink-volume @DEFAULT_SINK@ {max(0, min(100, v))}%")
    elif action in cmds:
        _spawn(cmds[action])
    else:
        return _redirect_page("unknown action")
    return _redirect_page(action)


@app.get("/workspace/switch")
async def workspace_get(index: int = 0):
    _spawn(f"wmctrl -s {max(0, int(index))}")
    return _redirect_page("workspace")


@app.get("/system/{action}")
async def system_get(action: str):
    if action == "status":
        return await status()
    cmds = {
        "lock": "loginctl lock-session",
        "sleep": "systemctl suspend",
        "logout": "loginctl terminate-user $USER",
        "reboot": "systemctl reboot",
        "shutdown": "systemctl poweroff",
    }
    if action not in cmds:
        return _redirect_page("unknown action")
    _spawn(cmds[action])
    return _redirect_page(action)


@app.get("/capture/screenshot")
async def screenshot_get():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = CAPTURE_DIR / f"screenshot_{ts}.png"
    _spawn(f"xfce4-screenshooter -f -s {out}")
    return _redirect_page("screenshot")


@app.get("/open")
async def open_url(url: str = ""):
    """Open a URL in Chrome on the host display."""
    if not re.match(r"^https?://[A-Za-z0-9._:/?=&%+-]+$", url):
        return _redirect_page("bad url")
    _spawn(f"google-chrome {shlex.quote(url)}")
    return _redirect_page("opening " + url)


# ---------------------------------------------------------- state (JSON)
@app.get("/services/ports")
async def services_ports():
    """Every listening TCP service: name, port, bind address, dashboard URL."""
    try:
        out = subprocess.check_output(["ss", "-tlnp"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return JSONResponse({"services": []})

    seen = {}
    for line in out.splitlines()[1:]:
        # LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:(("proc",pid=123,fd=4),...)
        m = re.match(r"LISTEN\s+\d+\s+\d+\s+(\S+?):(\d+)\s+\S+\s+users:\(\((.*)\)\)", line)
        if not m:
            continue
        addr, port, procs_raw = m.group(1), int(m.group(2)), m.group(3)
        names = re.findall(r'"([^"]+)",pid=(\d+)', procs_raw)
        if not names:
            continue
        proc_name, pid = names[0]
        key = (proc_name, port)
        if key in seen:
            continue
        seen[key] = {
            "proc": proc_name,
            "pid": int(pid),
            "service": _svc_name(proc_name, pid),
            "port": port,
            "addr": addr,
            "url": _svc_url(proc_name, port, addr),
        }

    services = sorted(seen.values(), key=lambda s: s["port"])
    return JSONResponse({"services": services})


def _svc_name(proc: str, pid: int) -> str:
    """Human-readable service name: known map > systemd unit > process name."""
    known = {
        "python3": None,  # resolved via systemd below (could be any of several)
        "node-MainThread": "Node app",
        "next-server (v1": "Next.js app",
        "llama-server": "llama.cpp LLM server",
        "openviking-serv": "OpenViking",
        "Discord": "Discord",
        "ulauncher": "ULAuncher",
        "kdeconnectd": "KDE Connect",
        "language_server": "Antigravity LSP",
        "antigravity": "Antigravity",
        "hermes": "Hermes",
        "cupsd": "CUPS printing",
    }
    if known.get(proc):
        return known[proc].rstrip("(").strip() if proc.startswith("next") else known[proc]
    try:
        unit = subprocess.check_output(
            ["systemctl", "--user", "status", str(pid)], text=True,
            stderr=subprocess.DEVNULL)
        for line in unit.splitlines():
            if line.strip().startswith("Loaded:"):
                # Loaded: loaded (/home/ahard/.config/systemd/user/xxx.service; ...)
                m = re.search(r"([A-Za-z0-9_.@-]+\.service)", line)
                if m:
                    return m.group(1).replace(".service", "")
    except Exception:
        pass
    return proc


def _svc_url(proc: str, port: int, addr: str) -> str | None:
    """Dashboard URL for services known to have a web UI."""
    web = {
        8080: "http://localhost:8080",      # llama.cpp server UI
        4173: "http://localhost:4173",      # gods-eye-view
        3000: "http://localhost:3000",      # next.js
        9443: "https://localhost:9443",     # portainer
        8083: "http://localhost:8083",      # noVNC
        6463: None,                          # discord local rpc, no web ui
    }
    if proc in ("llama-server", "node-MainThread", "next-server (v1"):
        return web.get(port)
    return web.get(port)


# ------------------------------------------------------- state (JSON) /logs, /system/status
@app.get("/logs")
async def logs(path: str = "journal", n: int = 120):
    try:
        if path == "journal":
            out = subprocess.check_output(
                ["journalctl", "-n", str(max(1, n)), "--no-pager", "-o", "short-iso"],
                text=True, stderr=subprocess.DEVNULL)
            return JSONResponse({"text": out[-8000:]})
        p = Path(path).resolve()
        if not str(p).startswith("/var/log"):
            return JSONResponse({"text": "path not allowed"}, status_code=400)
        if not p.exists():
            return JSONResponse({"text": "log not found"}, status_code=404)
        lines = p.read_text(errors="replace").splitlines()
        return JSONResponse({"text": "\n".join(lines[-max(1, n):])[-8000:]})
    except subprocess.CalledProcessError:
        return JSONResponse({"text": "log unavailable (permissions?)"}, status_code=200)
    except Exception as e:
        return JSONResponse({"text": str(e)}, status_code=500)


@app.get("/system/status")
async def status():
    ram = _ram()
    disk = _disk()
    gpu = _gpu()
    return JSONResponse({
        "cpu": _cpu(),
        "load": _load(),
        "ram_pct": ram["pct"],
        "ram_used": ram["used"],
        "ram_total": ram["total"],
        "disk_pct": disk["pct"],
        "disk_used": disk["used"],
        "disk_total": disk["total"],
        "gpu_pct": gpu["pct"],
        "gpu_temp": gpu["temp"],
        "cpu_temp": _cpu_temp(),
        "nvme_temp": _nvme_temp(),
        "battery": _battery(),
        "users": _users(),
        "network": _net(),
        "uptime": _uptime(),
        "processes": _processes(),
    })


# ------------------------------------------------------------- helpers
def _workspaces():
    try:
        out = subprocess.check_output(["wmctrl", "-d"], text=True)
        ws = []
        for line in out.splitlines():
            # format: IDX FLAG DG: GEOM VP: X,Y WA: X,Y WxH NAME...
            m = re.match(r"^(\d+)\s+[-*]\s+DG:\s+\S+\s+VP:\s+\S+\s+WA:\s+\S+\s+\S+\s+(.*)$", line)
            if m:
                ws.append({"index": int(m.group(1)), "name": m.group(2).strip() or f"WS {m.group(1)}"})
        return ws
    except Exception:
        return [{"index": 0, "name": "Main"}]


def _cpu():
    try:
        out = subprocess.check_output(["top", "-bn1"], text=True)
        for line in out.splitlines():
            if "Cpu(s):" in line:
                idle = float(line.split(",")[3].strip().split()[0].replace("id", "").strip())
                return round(100 - idle)
    except Exception:
        pass
    return 0


def _load():
    try:
        return open("/proc/loadavg").read().split()[:3]
    except Exception:
        return ["0", "0", "0"]


def _ram():
    try:
        out = subprocess.check_output(["free", "-m"], text=True)
        p = out.splitlines()[1].split()
        used, total = int(p[2]), int(p[1])
        return {"pct": round(used / total * 100), "used": f"{used/1024:.1f}G", "total": f"{total/1024:.1f}G"}
    except Exception:
        return {"pct": 0, "used": "—", "total": "—"}


def _disk():
    try:
        out = subprocess.check_output(["df", "-h", "/"], text=True)
        p = out.splitlines()[1].split()
        return {"pct": int(p[4].rstrip("%")), "used": p[2], "total": p[1]}
    except Exception:
        return {"pct": 0, "used": "—", "total": "—"}


def _gpu():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], text=True)
        util, temp = out.strip().splitlines()[0].split(", ")
        return {"pct": int(util), "temp": int(temp)}
    except Exception:
        return {"pct": 0, "temp": None}


def _cpu_temp():
    # Prefer Tdie (true die temp) over Tctl (includes AMD offset); same file otherwise
    for name in ("temp2_input", "temp1_input"):
        try:
            v = int(Path(f"/sys/class/hwmon/hwmon1/{name}").read_text()) / 1000
            return round(v)
        except Exception:
            continue
    return None


def _nvme_temp():
    try:
        v = int(Path("/sys/class/hwmon/hwmon0/temp1_input").read_text()) / 1000  # nvme Composite
        return round(v)
    except Exception:
        return None


def _battery():
    try:
        cap = Path("/sys/class/power_supply/BAT0/capacity")
        if cap.exists():
            st = Path("/sys/class/power_supply/BAT0/status")
            return f"{cap.read_text().strip()}% {st.read_text().strip()}"
    except Exception:
        pass
    return "AC"


def _users():
    try:
        return len(subprocess.check_output(["who"], text=True).splitlines())
    except Exception:
        return 0


def _net():
    try:
        out = subprocess.check_output(["ip", "-br", "addr"], text=True)
        rows = []
        for l in out.splitlines():
            if "UP" not in l or l.startswith("lo"):
                continue
            parts = l.split()
            iface = parts[0]
            ipv4 = next((p for p in parts[2:] if ":" not in p and p != "UP"), "")
            rows.append(f"{iface}  {ipv4}".strip())
        return rows or ["—"]
    except Exception:
        return ["—"]


def _uptime():
    try:
        return subprocess.check_output(["uptime", "-p"], text=True).strip().replace("up ", "")
    except Exception:
        return "—"


def _processes():
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,pcpu,pmem,comm", "--no-headers"], text=True)
        items = []
        for line in out.strip().splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 4 or parts[3].startswith("["):
                continue
            items.append({"pid": int(parts[0]), "cpu": float(parts[1]),
                          "mem": float(parts[2]), "name": parts[3]})
        items.sort(key=lambda x: x["cpu"], reverse=True)
        return items[:15]
    except Exception:
        return []



# ------------------------------------------------------------- webcam streaming
class WebcamManager:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.active_client: WebSocket | None = None
        self.lock = asyncio.Lock()

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                    await self.proc.stdin.wait_closed()
            except Exception:
                pass
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=1.5)
            except Exception:
                if self.proc:
                    self.proc.kill()
        self.proc = None
        self.active_client = None


cam_manager = WebcamManager()


@app.get("/cam/status")
async def cam_status():
    dev = Path(CAM_DEVICE)
    return JSONResponse({
        "device": CAM_DEVICE,
        "device_exists": dev.exists(),
        "streaming": cam_manager.proc is not None and cam_manager.proc.returncode is None,
    })


@app.websocket("/ws/cam")
async def websocket_cam(websocket: WebSocket):
    await websocket.accept()

    dev = Path(CAM_DEVICE)
    if not dev.exists():
        await websocket.send_json({
            "type": "error",
            "message": f"Virtual camera device {CAM_DEVICE} not found. Please run: sudo ./scripts/setup_v4l2loopback.sh"
        })
        await websocket.close(code=1008)
        return

    async with cam_manager.lock:
        await cam_manager.stop()
        try:
            # ffmpeg reads matroska/webm stream from stdin and writes raw yuv420p to v4l2 device
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-loglevel", "warning",
                "-use_wallclock_as_timestamps", "1",
                "-f", "matroska",
                "-i", "pipe:0",
                "-vf", "format=yuv420p",
                "-f", "v4l2",
                CAM_DEVICE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            cam_manager.proc = proc
            cam_manager.active_client = websocket
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Failed to spawn ffmpeg: {e}"
            })
            await websocket.close(code=1011)
            return

    await websocket.send_json({
        "type": "status",
        "status": "streaming",
        "device": CAM_DEVICE
    })

    try:
        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"]:
                if cam_manager.proc and cam_manager.proc.returncode is None:
                    try:
                        cam_manager.proc.stdin.write(message["bytes"])
                        await cam_manager.proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        break
            elif "text" in message and message["text"]:
                try:
                    msg = json.loads(message["text"])
                    if msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except Exception:
                    pass
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        async with cam_manager.lock:
            if cam_manager.active_client == websocket:
                await cam_manager.stop()


def ensure_certs():
    CERTS_DIR.mkdir(exist_ok=True)
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        print("Generating self-signed SSL certificate for HTTPS...")
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
                "-days", "3650", "-nodes", "-subj", "/CN=cmd-center"
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="CMD Center Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8081)), help="Port to listen on (default: 8081)")
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", "0.0.0.0"), help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS and run in plain HTTP mode")
    args = parser.parse_args()

    ssl_kwargs = {}
    use_ssl = not args.no_ssl and os.environ.get("USE_SSL", "1").lower() not in ("0", "false", "no")
    if use_ssl:
        ensure_certs()
        ssl_kwargs = {"ssl_certfile": str(CERT_FILE), "ssl_keyfile": str(KEY_FILE)}
        print(f"Starting CMD Center (HTTPS) on https://{args.host}:{args.port}")
    else:
        print(f"Starting CMD Center (HTTP) on http://{args.host}:{args.port}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", **ssl_kwargs)

