from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess, shlex, os, json, asyncio, re
from pathlib import Path
from datetime import datetime

APP_DIR = Path(__file__).resolve().parent.parent
WEBAPP = APP_DIR / "webapp" / "index.html"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config
WORKSPACES = [
    {"index": 0, "name": "Main"},
    {"index": 1, "name": "Web"},
    {"index": 2, "name": "Code"},
    {"index": 3, "name": "Media"},
    {"index": 4, "name": "Terminal"},
]
QUICK_COMMANDS = [
    {"label": "htop", "command": "kitty -- htop || xfce4-terminal -- htop || xterm -e htop"},
    {"label": "Disk", "command": "kitty -- bash -lc 'df -h; read' || xfce4-terminal -- bash -lc 'df -h'"},
    {"label": "Docker", "command": "kitty -- bash -lc 'docker ps -a; read' || xfce4-terminal -- bash -lc 'docker ps -a'"},
    {"label": "Net", "command": "kitty -- bash -lc 'ss -tulpn; read' || xfce4-terminal -- bash -lc 'ss -tulpn'"},
    {"label": "Journal", "command": "kitty -- bash -lc 'journalctl -p err -n 50 --no-pager; read' || xfce4-terminal -- bash -lc 'journalctl -p err -n 50'"},
    {"label": "Update", "command": "kitty -- bash -lc 'sudo apt update && sudo apt upgrade -y; read' || xfce4-terminal -- bash -lc 'sudo apt update && sudo apt upgrade -y'"},
]

# Models
class PromptRequest(BaseModel):
    prompt: str

class CommandRequest(BaseModel):
    command: str

class VolumeRequest(BaseModel):
    volume: int

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=WEBAPP.read_text())

@app.get("/config")
async def config():
    return JSONResponse({"workspaces": WORKSPACES, "quick_commands": QUICK_COMMANDS})

@app.post("/media/play")
async def media_play():
    return await _run_dbus(["org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Play"]) or await _run_cmd("playerctl play")

@app.post("/media/pause")
async def media_pause():
    return await _run_dbus(["org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Pause"]) or await _run_cmd("playerctl pause")

@app.post("/media/prev")
async def media_prev():
    return await _run_cmd("playerctl previous")

@app.post("/media/next")
async def media_next():
    return await _run_cmd("playerctl next")

@app.post("/media/mute")
async def media_mute():
    return await _run_cmd("pactl set-sink-mute @DEFAULT_SINK@ 1")

@app.post("/media/unmute")
async def media_unmute():
    return await _run_cmd("pactl set-sink-mute @DEFAULT_SINK@ 0")

@app.post("/media/volume")
async def media_volume(req: VolumeRequest):
    v = max(0, min(100, req.volume))
    return await _run_cmd(f"pactl set-sink-volume @DEFAULT_SINK@ {v}%")

@app.post("/workspace/switch")
async def workspace_switch(req: CommandRequest):
    # xdotool workspace switch for common WMs; Xfce uses wmctrl
    return await _run_cmd(f"wmctrl -s {req.command}")

@app.post("/command/run")
async def command_run(req: CommandRequest):
    # Run quick command in background via xfce4-terminal or kitty
    cmd = req.command
    # Sanitize: allow expected patterns only for safety on LAN-open endpoint
    allowed = re.compile(r'^(kitty|xfce4-terminal|xterm|bash|sudo|apt|htop|docker|ss|journalctl|df|playerctl|pactl|wmctrl|reboot|shutdown|systemctl)')
    if not allowed.search(cmd):
        return JSONResponse({"ok": False, "error": "command not allowed"}, status_code=400)
    asyncio.get_event_loop().run_in_executor(None, lambda: subprocess.Popen(cmd, shell=True, start_new_session=True))
    return JSONResponse({"ok": True})

@app.post("/system/lock")
async def system_lock():
    return await _run_cmd("loginctl lock-session $(loginctl | grep $(whoami) | awk 'NR==1{print $1}')")

@app.post("/system/sleep")
async def system_sleep():
    return await _run_cmd("systemctl suspend")

@app.post("/system/logout")
async def system_logout():
    return await _run_cmd("loginctl terminate-user $(whoami)")

@app.get("/system/status")
async def system_status():
    # Use local probes without external calls
    cpu = _read_cpu()
    ram = _read_ram()
    disk = _read_disk()
    gpu = _read_gpu()
    temps = _read_temps()
    battery = _read_battery()
    users = _read_users()
    net = _read_net()
    top_cpu = _top("cpu")
    top_mem = _top("mem")
    uptime = _read_uptime()
    return JSONResponse({
        "cpu": cpu, "ram_pct": ram["pct"], "ram_used": ram["used"], "ram_total": ram["total"],
        "disk_pct": disk["pct"], "disk_used": disk["used"], "disk_total": disk["total"],
        "gpu_pct": gpu["pct"], "gpu_temp": gpu["temp"], "temps": temps,
        "battery": battery, "users": users, "network": net,
        "top_cpu": top_cpu, "top_mem": top_mem, "uptime": uptime,
    })

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "hermes_prompt":
                reply = await _hermes_prompt(data.get("prompt", ""))
                await ws.send_text(json.dumps({"type":"hermes_reply","text": reply}))
    except WebSocketDisconnect:
        pass

async def _hermes_prompt(prompt: str) -> str:
    # Use Hermes CLI print mode for deterministic, non-interactive execution
    try:
        p = await asyncio.create_subprocess_exec(
            "hermes", "--print", "--no-usage-file", prompt,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await p.communicate()
        out = out.decode("utf-8", "ignore").strip()
        if out:
            return out
        return "Hermes returned no output. " + (err.decode("utf-8","ignore").strip()[:200] or "")
    except Exception as e:
        return f"Hermes error: {e}"

async def _run_cmd(cmd: str):
    try:
        p = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

async def _run_dbus(args):
    try:
        p = await asyncio.create_subprocess_exec("dbus-send", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"ok": False}, status_code=500)

# Local system probes
def _read_cpu():
    try:
        with open("/proc/loadavg") as f: l1,l2,l3,_ = f.read().split()[:4]
        return f"{round(float(l1)*100)}%"
    except Exception:
        return "—"

def _read_ram():
    try:
        out = subprocess.check_output(["free","-m"], text=True)
        lines = out.splitlines()
        mem = lines[1].split()
        used, total = int(mem[2]), int(mem[1])
        return {"pct": int(used/total*100), "used": f"{used}MB", "total": f"{total}MB"}
    except Exception:
        return {"pct": 0, "used": "—", "total": "—"}

def _read_disk():
    try:
        out = subprocess.check_output(["df","-h","/"], text=True)
        line = out.splitlines()[1].split()
        total, used, pct = line[1], line[2], int(line[4].replace('%',''))
        return {"pct": pct, "used": used, "total": total}
    except Exception:
        return {"pct": 0, "used": "—", "total": "—"}

def _read_gpu():
    try:
        out = subprocess.check_output(["nvidia-smi","--query-gpu=utilization.gpu,temperature.gpu","--format=csv,noheader,nounits"], text=True)
        util, temp = out.strip().splitlines()[0].split(", ")
        return {"pct": int(util), "temp": int(temp)}
    except Exception:
        return {"pct": 0, "temp": None}

def _read_temps():
    try:
        out = subprocess.check_output(["sensors","-u"], text=True, stderr=subprocess.DEVNULL)
        vals=[]
        for line in out.splitlines():
            if "_input:" in line:
                label=line.split()[0].strip(":"); vals.append({"label":label,"value":line.split()[-1],"unit":"C"})
        return vals[:8]
    except Exception:
        return []

def _read_battery():
    try:
        if Path("/sys/class/power_supply/BAT0/capacity").exists():
            p=Path("/sys/class/power_supply/BAT0/capacity"); s=Path("/sys/class/power_supply/BAT0/status")
            return f"{p.read_text().strip()}% {s.read_text().strip()}"
    except Exception:
        pass
    return "AC"

def _read_users():
    try:
        out = subprocess.check_output(["who"], text=True)
        return str(len(out.splitlines()))
    except Exception:
        return "—"

def _read_net():
    try:
        out = subprocess.check_output(["ip","-br","addr"], text=True)
        lines = [l for l in out.splitlines() if "UP" in l and not l.startswith("lo ")]
        return "; ".join(x.strip() for x in lines) or "—"
    except Exception:
        return "—"

def _read_uptime():
    try:
        out = subprocess.check_output(["uptime","-p"], text=True).strip()
        return out.replace("up ","")
    except Exception:
        return "—"

def _top(mode):
    try:
        field = 2 if mode=="cpu" else 3
        sort_flag = "-pcpu" if mode=="cpu" else "-pmem"
        out = subprocess.check_output(["ps","-eo","pid,pcpu,pmem,comm",f"--sort=-{sort_flag}"], text=True)
        lines = out.strip().splitlines()[1:4]
        return [{"name": l.split()[-1], "pct": l.split()[field]} for l in lines]
    except Exception:
        return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
