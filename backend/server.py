from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess, json, asyncio, re
from pathlib import Path
from datetime import datetime

APP_DIR = Path(__file__).resolve().parent.parent
WEBAPP = APP_DIR / "webapp" / "index.html"
CAPTURE_DIR = APP_DIR / "captures"
CAPTURE_DIR.mkdir(exist_ok=True)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACES = [
    {"index": 0, "name": "Main"},
    {"index": 1, "name": "Web"},
    {"index": 2, "name": "Code"},
    {"index": 3, "name": "Media"},
    {"index": 4, "name": "Terminal"},
]

class CommandRequest(BaseModel):
    command: str

class VolumeRequest(BaseModel):
    volume: int

class CaptureRequest(BaseModel):
    action: str = "screenshot"

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=WEBAPP.read_text())

@app.get("/config")
async def config():
    return JSONResponse({"workspaces": WORKSPACES})

@app.post("/media/play")
async def media_play():
    return await _run("playerctl play")

@app.post("/media/pause")
async def media_pause():
    return await _run("playerctl pause")

@app.post("/media/prev")
async def media_prev():
    return await _run("playerctl previous")

@app.post("/media/next")
async def media_next():
    return await _run("playerctl next")

@app.post("/media/mute")
async def media_mute():
    return await _run("pactl set-sink-mute @DEFAULT_SINK@ 1")

@app.post("/media/unmute")
async def media_unmute():
    return await _run("pactl set-sink-mute @DEFAULT_SINK@ 0")

@app.post("/media/volume")
async def media_volume(req: VolumeRequest):
    return await _run(f"pactl set-sink-volume @DEFAULT_SINK@ {max(0, min(100, req.volume))}%")

@app.post("/workspace/switch")
async def workspace_switch(req: CommandRequest):
    return await _run(f"wmctrl -s {req.command}")

@app.post("/apps/launch")
async def apps_launch(req: CommandRequest):
    cmd = req.command
    if not re.search(r'^(firefox|thunar|xfce4-terminal|mousepad|xfce4-calculator|xfce4-settings-manager|htop|btop|docker)$', cmd):
        return JSONResponse({"ok": False, "error": "command not allowed"}, status_code=400)
    asyncio.get_event_loop().run_in_executor(None, lambda: subprocess.Popen(cmd, shell=True, start_new_session=True))
    return JSONResponse({"ok": True})

@app.post("/apps/terminal")
async def apps_terminal():
    return await _run("xfce4-terminal")

@app.post("/apps/files")
async def apps_files():
    return await _run("thunar")

@app.post("/apps/browser")
async def apps_browser():
    return await _run("firefox")

@app.post("/apps/editor")
async def apps_editor():
    return await _run("mousepad")

@app.post("/command/run")
async def command_run(req: CommandRequest):
    cmd = req.command
    if not re.search(r'^(kitty|xfce4-terminal|xterm|gnome-terminal|bash|sudo|apt|htop|btop|docker|ss|journalctl|df|playerctl|pactl|wmctrl|reboot|shutdown|systemctl|nmcli|ping|curl|ncdu|speedtest)', cmd):
        return JSONResponse({"ok": False, "error": "command not allowed"}, status_code=400)
    asyncio.get_event_loop().run_in_executor(None, lambda: subprocess.Popen(cmd, shell=True, start_new_session=True))
    return JSONResponse({"ok": True})

@app.get("/logs")
async def logs(path: str = "/var/log/syslog", n: int = 80):
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return JSONResponse({"text": "log not found"}, status_code=404)
    try:
        lines = p.read_text(errors="replace").splitlines()
        return JSONResponse({"text": "\n".join(lines[-max(1, n):])})
    except Exception as e:
        return JSONResponse({"text": str(e)}, status_code=500)

@app.post("/capture/screenshot")
async def capture_screenshot():
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = CAPTURE_DIR / f"screenshot_{ts}.png"
        p = await asyncio.create_subprocess_exec("xfce4-screenshooter", "-f", "-s", str(out), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()
        if out.exists():
            return JSONResponse({"ok": True, "file": str(out)})
        return JSONResponse({"ok": False, "error": "screenshot tool failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.post("/capture/record")
async def capture_record(req: CaptureRequest):
    if req.action == "stop":
        return await _run("pkill -f recordmydesktop || true")
    if req.action != "toggle":
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = CAPTURE_DIR / f"screen_{ts}.ogv"
    asyncio.get_event_loop().run_in_executor(None, lambda: subprocess.Popen(["recordmydesktop", "--no-sound", "--output", str(out)], start_new_session=True))
    return JSONResponse({"ok": True, "file": str(out)})

@app.post("/system/lock")
async def system_lock():
    return await _run("loginctl lock-session $(loginctl | grep $(whoami) | awk 'NR==1{print $1}')")

@app.post("/system/sleep")
async def system_sleep():
    return await _run("systemctl suspend")

@app.post("/system/logout")
async def system_logout():
    return await _run("loginctl terminate-user $(whoami)")

@app.post("/system/reboot")
async def system_reboot():
    return await _run("systemctl reboot")

@app.post("/system/shutdown")
async def system_shutdown():
    return await _run("systemctl poweroff")

@app.get("/system/status")
async def system_status():
    return JSONResponse({
        "cpu": _read_cpu(),
        "ram_pct": _read_ram()["pct"],
        "ram_used": _read_ram()["used"],
        "ram_total": _read_ram()["total"],
        "disk_pct": _read_disk()["pct"],
        "disk_used": _read_disk()["used"],
        "disk_total": _read_disk()["total"],
        "gpu_pct": _read_gpu()["pct"],
        "gpu_temp": _read_gpu()["temp"],
        "temps": _read_temps(),
        "battery": _read_battery(),
        "users": _read_users(),
        "network": _read_net(),
        "top_cpu": _top("cpu"),
        "top_mem": _top("mem"),
        "uptime": _read_uptime(),
        "processes": _process_list(),
    })

async def _run(cmd: str):
    try:
        p = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

def _read_cpu():
    try:
        with open("/proc/loadavg") as f:
            l1 = f.read().split()[0]
        return f"{round(float(l1) * 100)}%"
    except Exception:
        return "—"

def _read_ram():
    try:
        out = subprocess.check_output(["free", "-m"], text=True)
        parts = out.splitlines()[1].split()
        used, total = int(parts[2]), int(parts[1])
        return {"pct": int(used / total * 100), "used": f"{used}MB", "total": f"{total}MB"}
    except Exception:
        return {"pct": 0, "used": "—", "total": "—"}

def _read_disk():
    try:
        out = subprocess.check_output(["df", "-h", "/"], text=True)
        parts = out.splitlines()[1].split()
        return {"pct": int(parts[4].replace('%', '')), "used": parts[2], "total": parts[1]}
    except Exception:
        return {"pct": 0, "used": "—", "total": "—"}

def _read_gpu():
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"], text=True)
        util, temp = out.strip().splitlines()[0].split(", ")
        return {"pct": int(util), "temp": int(temp)}
    except Exception:
        return {"pct": 0, "temp": None}

def _read_temps():
    try:
        out = subprocess.check_output(["sensors", "-u"], text=True, stderr=subprocess.DEVNULL)
        vals = []
        for line in out.splitlines():
            if "_input:" in line:
                label = line.split()[0].strip(":")
                vals.append({"label": label, "value": line.split()[-1], "unit": "C"})
        return vals[:8]
    except Exception:
        return []

def _read_battery():
    try:
        cap = Path("/sys/class/power_supply/BAT0/capacity")
        st = Path("/sys/class/power_supply/BAT0/status")
        if cap.exists() and st.exists():
            return f"{cap.read_text().strip()}% {st.read_text().strip()}"
    except Exception:
        pass
    return "AC"

def _read_users():
    try:
        return str(len(subprocess.check_output(["who"], text=True).splitlines()))
    except Exception:
        return "—"

def _read_net():
    try:
        out = subprocess.check_output(["ip", "-br", "addr"], text=True)
        lines = [l for l in out.splitlines() if "UP" in l and not l.startswith("lo ")]
        return "; ".join(x.strip() for x in lines) or "—"
    except Exception:
        return "—"

def _read_uptime():
    try:
        out = subprocess.check_output(["uptime", "-p"], text=True).strip()
        return out.replace("up ", "")
    except Exception:
        return "—"

def _top(mode):
    try:
        field = 1 if mode == "cpu" else 2
        sort_flag = "-pcpu" if mode == "cpu" else "-pmem"
        out = subprocess.check_output(["ps", "-eo", "pid,pcpu,pmem,comm", f"--sort=-{sort_flag}", "--no-headers"], text=True)
        items = []
        for line in out.strip().splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            name = parts[3]
            if name == "ps" or name.startswith("["):
                continue
            items.append({"name": name, "pct": parts[field]})
            if len(items) >= 3:
                break
        return items
    except Exception:
        return []

def _process_list():
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,pcpu,pmem,comm", "--no-headers"], text=True)
        items = []
        for line in out.strip().splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            name = parts[3]
            if name.startswith("["):
                continue
            items.append({"pid": int(parts[0]), "cpu": float(parts[1]), "mem": float(parts[2]), "name": name})
        items.sort(key=lambda x: x["cpu"], reverse=True)
        return items[:20]
    except Exception:
        return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
