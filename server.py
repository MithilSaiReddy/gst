"""
server.py  —  GST Agent Dashboard Server (FastAPI)

Install:
    pip install fastapi uvicorn requests

Run:
    python server.py

Opens at: http://localhost:8000
"""

import json
import os
import time
import asyncio
import threading
from datetime import datetime
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── PATHS ───────────────────────────────────────────────────────────────────

_HERE        = Path(__file__).parent.resolve()
BUJJI_URL    = os.getenv("BUJJI_URL",  "http://localhost:7337")
WORKSPACE    = Path(os.getenv("WORKSPACE", str(_HERE / "workspace")))

LOGS_PATH    = WORKSPACE / "logs"    / "activity.log"
OUTPUTS_DIR  = WORKSPACE / "outputs"
INVOICES_DIR = WORKSPACE / "invoices"
CRON_PATH    = WORKSPACE / "cron"    / "jobs.json"

for d in [WORKSPACE, OUTPUTS_DIR, INVOICES_DIR, LOGS_PATH.parent, CRON_PATH.parent]:
    d.mkdir(parents=True, exist_ok=True)
if not LOGS_PATH.exists():
    LOGS_PATH.write_text("")
if not CRON_PATH.exists():
    CRON_PATH.write_text("[]")

# ─── SYNC BUJJI WORKSPACE ────────────────────────────────────────────────────

def sync_bujji_workspace():
    cfg_path = Path.home() / ".bujji" / "config.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text())
        cfg.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = str(WORKSPACE)
        cfg_path.write_text(json.dumps(cfg, indent=2))
        print(f"  [OK] Bujji workspace synced to: {WORKSPACE}")
    except Exception as e:
        print(f"  [WARN] Could not sync bujji config: {e}")

sync_bujji_workspace()

# ─── STATE ───────────────────────────────────────────────────────────────────

agent_status = {"running": False, "task": "Idle", "last_run": None}
sse_queues: list = []

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _rjson(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

def _wjson(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))

def tail_log(n: int = 100):
    try:
        return LOGS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []

def log_write(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open(LOGS_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    for q in list(sse_queues):
        try:
            q.put_nowait(line)
        except Exception:
            pass

# ─── AGENT RUNNER ────────────────────────────────────────────────────────────

def run_agent(prompt: str):
    agent_status["running"] = True
    agent_status["task"]    = prompt[:80]
    log_write(f"[AGENT] Starting: {prompt[:120]}")
    try:
        resp = requests.post(
            f"{BUJJI_URL}/api/chat",
            json={"message": prompt, "session_id": "dashboard"},
            timeout=300,
            stream=True,
        )
        resp.raise_for_status()
        token_buf = []
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                evt   = json.loads(line)
                etype = evt.get("type", "")
                if etype == "token":
                    token_buf.append(evt.get("content", ""))
                elif etype == "tool_start":
                    if token_buf:
                        log_write("[AGENT] " + "".join(token_buf).strip()); token_buf = []
                    args    = evt.get("args", {})
                    arg_str = ", ".join(f"{k}={str(v)[:50]}" for k, v in args.items())
                    log_write(f"[TOOL] >> {evt.get('name','?')}({arg_str})")
                elif etype == "tool_done":
                    r = evt.get("result", "")[:200].replace("\n", " ")
                    log_write(f"[TOOL] OK {evt.get('name','?')} -> {r}")
                elif etype == "done":
                    if token_buf:
                        log_write("[AGENT] " + "".join(token_buf).strip()); token_buf = []
                    for fl in (evt.get("content") or "").strip().splitlines():
                        if fl.strip():
                            log_write(f"[RESULT] {fl}")
                elif etype == "error":
                    log_write(f"[ERROR] {evt.get('content','')}")
            except json.JSONDecodeError:
                if line.strip():
                    log_write(f"[AGENT] {line}")
        if token_buf:
            log_write("[AGENT] " + "".join(token_buf).strip())
    except requests.exceptions.ConnectionError:
        log_write(f"[ERROR] Cannot reach Bujji at {BUJJI_URL}")
        log_write("[INFO]  Run: cd bujji && python main.py serve")
    except Exception as e:
        log_write(f"[ERROR] {type(e).__name__}: {e}")
    finally:
        agent_status["running"] = False
        agent_status["task"]    = "Idle"
        agent_status["last_run"] = datetime.now().isoformat()
        log_write("[AGENT] Done. Going idle.")

def trigger_agent(prompt: str):
    threading.Thread(target=run_agent, args=(prompt,), daemon=True).start()

def process_prompt() -> str:
    return (
        f"Step 1: Call scan_invoice_directory with directory='{INVOICES_DIR}' and move_processed=false. "
        f"Step 2: Call compile_gstr1. "
        f"Step 3: Call generate_gstr3b. "
        f"Step 4: Call generate_mismatch_report. "
        f"Step 5: Print SUMMARY with period, invoices, taxable value, tax liability, ITC, net payable, flags."
    )

# ─── APP ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="GST Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Static ──
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard.html", response_class=HTMLResponse)
async def serve_dashboard():
    p = _HERE / "dashboard.html"
    return HTMLResponse(p.read_text(encoding="utf-8") if p.exists() else "<h1>dashboard.html not found</h1>")

# ── Status / Config ──
@app.get("/api/status")
async def status():
    return agent_status

@app.get("/api/config")
async def config():
    return {"invoices_dir": str(INVOICES_DIR), "outputs_dir": str(OUTPUTS_DIR), "workspace": str(WORKSPACE)}

# ── SSE log stream ──
@app.get("/api/logs/stream")
async def logs_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    sse_queues.append(q)
    history = tail_log(30)

    async def gen():
        try:
            for line in history:
                yield f"data: {line}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            try: sse_queues.remove(q)
            except ValueError: pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/logs")
async def logs(n: int = 100):
    return {"lines": tail_log(n)}

# ── Run agent ──
class RunReq(BaseModel):
    prompt: str

@app.post("/api/run")
async def run(req: RunReq):
    if not req.prompt.strip():
        raise HTTPException(400, "prompt required")
    if agent_status["running"]:
        raise HTTPException(409, "Agent already running")
    trigger_agent(req.prompt.strip())
    return {"status": "started"}

# ── Upload — auto-triggers agent immediately ──
@app.post("/api/upload")
async def upload(request: Request):
    filename = request.headers.get("X-Filename", f"invoice_{int(time.time())}.pdf")
    body     = await request.body()
    if not body:
        raise HTTPException(400, "No file data")
    dest = INVOICES_DIR / filename
    dest.write_bytes(body)
    log_write(f"[UPLOAD] {filename} ({len(body):,} bytes) saved")
    if not agent_status["running"]:
        log_write(f"[AUTO] Agent triggered for {filename}")
        trigger_agent(process_prompt())
    else:
        log_write(f"[AUTO] Agent busy — {filename} queued for next run")
    return {"status": "uploaded", "file": filename, "agent": "triggered"}

# ── Outputs ──
@app.get("/api/outputs")
async def outputs():
    if not OUTPUTS_DIR.exists():
        return {"files": []}
    return {"files": [
        {"name": fp.name, "size": fp.stat().st_size,
         "modified": datetime.fromtimestamp(fp.stat().st_mtime).isoformat()}
        for fp in sorted(OUTPUTS_DIR.iterdir()) if fp.is_file()
    ]}

@app.get("/api/outputs/{filename}")
async def download(filename: str):
    fp = OUTPUTS_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "File not found")
    ct = {"csv": "text/csv", "json": "application/json", "pdf": "application/pdf"}.get(fp.suffix[1:], "application/octet-stream")
    return FileResponse(str(fp), media_type=ct,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

# ── Settings — save/load Unsiloed key into bujji config ──
@app.get("/api/settings")
async def get_settings():
    cfg_path = Path.home() / ".bujji" / "config.json"
    if not cfg_path.exists():
        return {"unsiloed_api_key": "", "bujji_configured": False}
    cfg = json.loads(cfg_path.read_text())
    key = cfg.get("tools", {}).get("unsiloed", {}).get("api_key", "")
    masked = (key[:8] + "..." + key[-4:]) if len(key) > 12 else ("set" if key else "")
    return {
        "unsiloed_api_key_set": bool(key),
        "unsiloed_api_key_preview": masked,
        "bujji_configured": bool(cfg.get("active_provider") or cfg.get("providers")),
        "workspace": str(WORKSPACE),
    }

class SettingsReq(BaseModel):
    unsiloed_api_key: str = ""

@app.post("/api/settings")
async def save_settings(req: SettingsReq):
    cfg_path = Path.home() / ".bujji" / "config.json"
    if not cfg_path.exists():
        raise HTTPException(400, "Bujji not configured. Run: cd bujji && python main.py onboard")
    cfg = json.loads(cfg_path.read_text())
    cfg.setdefault("tools", {}).setdefault("unsiloed", {})["api_key"] = req.unsiloed_api_key
    cfg_path.write_text(json.dumps(cfg, indent=2))
    log_write(f"[SETTINGS] Unsiloed API key {'set' if req.unsiloed_api_key else 'cleared'}")
    return {"status": "saved"}

# ── Cron ──
@app.get("/api/cron")
async def cron_list():
    return {"jobs": _rjson(CRON_PATH, [])}

class CronReq(BaseModel):
    name: str
    prompt: str
    schedule_label: str = ""
    interval_minutes: int = 1440

@app.post("/api/cron")
async def cron_create(job: CronReq):
    jobs = _rjson(CRON_PATH, [])
    new  = {"id": int(time.time()), "name": job.name, "prompt": job.prompt,
            "schedule_label": job.schedule_label, "interval_minutes": job.interval_minutes,
            "last_run": None, "enabled": True}
    jobs.append(new); _wjson(CRON_PATH, jobs)
    return {"status": "created", "job": new}

@app.put("/api/cron/{job_id}")
async def cron_update(job_id: int, request: Request):
    data = await request.json()
    jobs = _rjson(CRON_PATH, [])
    for j in jobs:
        if j.get("id") == job_id:
            j.update({k: v for k, v in data.items() if k != "id"})
            _wjson(CRON_PATH, jobs)
            return {"status": "updated", "job": j}
    raise HTTPException(404, "Not found")

@app.delete("/api/cron/{job_id}")
async def cron_delete(job_id: int):
    jobs = _rjson(CRON_PATH, [])
    new  = [j for j in jobs if j.get("id") != job_id]
    if len(new) == len(jobs): raise HTTPException(404, "Not found")
    _wjson(CRON_PATH, new); return {"status": "deleted"}

@app.patch("/api/cron/{job_id}/toggle")
async def cron_toggle(job_id: int):
    jobs = _rjson(CRON_PATH, [])
    for j in jobs:
        if j.get("id") == job_id:
            j["enabled"] = not j.get("enabled", True)
            _wjson(CRON_PATH, jobs)
            return {"status": "toggled", "enabled": j["enabled"]}
    raise HTTPException(404, "Not found")

# ── Cron background thread ──
def _cron_runner():
    while True:
        time.sleep(60)
        jobs = _rjson(CRON_PATH, [])
        now  = datetime.now()
        changed = False
        for job in jobs:
            if not job.get("enabled", True): continue
            last = job.get("last_run")
            if last:
                elapsed = (now - datetime.fromisoformat(last)).total_seconds() / 60
                if elapsed < job.get("interval_minutes", 1440): continue
            if not agent_status["running"]:
                log_write(f"[CRON] {job['name']}")
                job["last_run"] = now.isoformat(); changed = True
                trigger_agent(job["prompt"]); break
        if changed: _wjson(CRON_PATH, jobs)

@app.on_event("startup")
async def startup():
    threading.Thread(target=_cron_runner, daemon=True).start()
    log_write("[SERVER] Started")
    print(f"\n{'='*52}\n  GST Agent  →  http://localhost:8000\n  Workspace  →  {WORKSPACE}\n{'='*52}")
    print(f"\n  Also run:  cd bujji && python main.py serve\n")

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False, log_level="warning")