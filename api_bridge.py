"""
api_bridge.py
Thin HTTP API that sits between the dashboard (HTML/JS) and the Bujji agent.
Run: python api_bridge.py
Default port: 8000

Endpoints:
  POST /run          — Trigger agent with a prompt
  POST /upload       — Upload invoice PDF
  GET  /logs/stream  — SSE stream of activity.log
  GET  /logs         — Last N log lines
  GET  /outputs      — List output files
  GET  /outputs/<f>  — Download output file
  GET  /status       — Agent status
  GET  /cron         — List cron jobs
  POST /cron         — Create cron job
  PUT  /cron/<id>    — Update cron job
  DELETE /cron/<id>  — Delete cron job
  PATCH /cron/<id>/toggle — Enable/disable cron job
"""

import json
import os
import time
import threading
import queue
import shutil
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BUJJI_URL  = os.getenv("BUJJI_URL", "http://localhost:7337")
WORKSPACE  = os.getenv("WORKSPACE",  os.path.expanduser("~/.bujji/workspace"))
PORT       = int(os.getenv("API_PORT", "8000"))

LOGS_PATH   = os.path.join(WORKSPACE, "logs", "activity.log")
OUTPUTS_DIR = os.path.join(WORKSPACE, "outputs")
INVOICES_DIR = os.path.join(WORKSPACE, "invoices")
CRON_PATH   = os.path.join(WORKSPACE, "cron", "jobs.json")

# SSE subscribers
sse_subscribers: list[queue.Queue] = []
agent_status = {"running": False, "task": "Idle", "last_run": None}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def tail_log(n=100) -> list:
    if not os.path.exists(LOGS_PATH):
        return []
    with open(LOGS_PATH) as f:
        lines = f.readlines()
    return [l.rstrip() for l in lines[-n:]]


def broadcast_sse(data: str):
    dead = []
    for q in sse_subscribers:
        try:
            q.put_nowait(data)
        except queue.Full:
            dead.append(q)
    for q in dead:
        sse_subscribers.remove(q)


def watch_log():
    """Background thread — tails activity.log and broadcasts to SSE subscribers."""
    os.makedirs(os.path.dirname(LOGS_PATH), exist_ok=True)
    if not os.path.exists(LOGS_PATH):
        open(LOGS_PATH, "w").close()

    with open(LOGS_PATH) as f:
        f.seek(0, 2)  # Seek to end
        while True:
            line = f.readline()
            if line:
                broadcast_sse(line.strip())
            else:
                time.sleep(0.5)


def run_agent(prompt: str):
    """Call Bujji's HTTP API with a prompt."""
    agent_status["running"] = True
    agent_status["task"] = prompt[:60]
    try:
        resp = requests.post(
            f"{BUJJI_URL}/api/chat",
            json={"message": prompt, "session_id": "dashboard"},
            timeout=120,
            stream=True,
        )
        for chunk in resp.iter_lines():
            if chunk:
                decoded = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                broadcast_sse(f"[AGENT] {decoded}")
    except requests.exceptions.ConnectionError:
        broadcast_sse("[ERROR] Cannot connect to Bujji. Is it running? (python main.py serve)")
    except Exception as e:
        broadcast_sse(f"[ERROR] {e}")
    finally:
        agent_status["running"] = False
        agent_status["task"] = "Idle"
        agent_status["last_run"] = datetime.now().isoformat()

# ─── HTTP HANDLER ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # Suppress default access log

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        for k, v in cors_headers().items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_cors_ok(self):
        self.send_response(204)
        for k, v in cors_headers().items():
            self.send_header(k, v)
        self.end_headers()

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_cors_ok()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # ── Status ──────────────────────────────────────────
        if path == "/status":
            return self.send_json(agent_status)

        # ── SSE log stream ───────────────────────────────────
        elif path == "/logs/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            for k, v in cors_headers().items():
                self.send_header(k, v)
            self.end_headers()

            q = queue.Queue(maxsize=200)
            sse_subscribers.append(q)

            # Send last 20 lines immediately
            for line in tail_log(20):
                msg = f"data: {line}\n\n"
                self.wfile.write(msg.encode())
            self.wfile.flush()

            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        # Heartbeat ping
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                if q in sse_subscribers:
                    sse_subscribers.remove(q)

        # ── Log lines ────────────────────────────────────────
        elif path == "/logs":
            qs = parse_qs(parsed.query)
            n = int(qs.get("n", [100])[0])
            return self.send_json({"lines": tail_log(n)})

        # ── List outputs ─────────────────────────────────────
        elif path == "/outputs":
            if not os.path.exists(OUTPUTS_DIR):
                return self.send_json({"files": []})
            files = []
            for fname in sorted(os.listdir(OUTPUTS_DIR)):
                fpath = os.path.join(OUTPUTS_DIR, fname)
                if os.path.isfile(fpath):
                    files.append({
                        "name": fname,
                        "size": os.path.getsize(fpath),
                        "modified": datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat(),
                    })
            return self.send_json({"files": files})

        # ── Download output file ──────────────────────────────
        elif path.startswith("/outputs/"):
            fname = path[len("/outputs/"):]
            fpath = os.path.join(OUTPUTS_DIR, fname)
            if not os.path.exists(fpath):
                return self.send_json({"error": "File not found"}, 404)
            with open(fpath, "rb") as f:
                body = f.read()
            ext = fname.rsplit(".", 1)[-1].lower()
            ct = {"csv": "text/csv", "json": "application/json", "pdf": "application/pdf"}.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", len(body))
            for k, v in cors_headers().items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        # ── Cron jobs ─────────────────────────────────────────
        elif path == "/cron":
            jobs = read_json(CRON_PATH, [])
            return self.send_json({"jobs": jobs})

        else:
            return self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # ── Trigger agent run ─────────────────────────────────
        if path == "/run":
            body = self.read_body()
            prompt = body.get("prompt", "").strip()
            if not prompt:
                return self.send_json({"error": "prompt required"}, 400)
            if agent_status["running"]:
                return self.send_json({"error": "Agent already running"}, 409)
            threading.Thread(target=run_agent, args=(prompt,), daemon=True).start()
            return self.send_json({"status": "started", "prompt": prompt})

        # ── Upload invoice ─────────────────────────────────────
        elif path == "/upload":
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return self.send_json({"error": "No file data"}, 400)

            # Simple multipart: read raw bytes and save
            raw = self.rfile.read(length)

            # Try to extract filename from Content-Disposition header
            fname = self.headers.get("X-Filename", f"invoice_{int(time.time())}.pdf")
            os.makedirs(INVOICES_DIR, exist_ok=True)
            dest = os.path.join(INVOICES_DIR, fname)

            with open(dest, "wb") as f:
                f.write(raw)

            broadcast_sse(f"[UPLOAD] File received: {fname} ({len(raw)} bytes)")
            return self.send_json({"status": "uploaded", "file": fname, "size": len(raw)})

        # ── Add cron job ──────────────────────────────────────
        elif path == "/cron":
            body = self.read_body()
            required = ["name", "prompt", "schedule_label", "interval_minutes"]
            if not all(k in body for k in ["name", "prompt"]):
                return self.send_json({"error": "name and prompt required"}, 400)
            jobs = read_json(CRON_PATH, [])
            new_job = {
                "id": int(time.time()),
                "name": body["name"],
                "prompt": body["prompt"],
                "schedule_label": body.get("schedule_label", "Custom"),
                "interval_minutes": int(body.get("interval_minutes", 1440)),
                "last_run": None,
                "enabled": True,
            }
            jobs.append(new_job)
            write_json(CRON_PATH, jobs)
            return self.send_json({"status": "created", "job": new_job})

        else:
            return self.send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")

        # PUT /cron/<id>
        if len(parts) == 2 and parts[0] == "cron":
            job_id = int(parts[1])
            body = self.read_body()
            jobs = read_json(CRON_PATH, [])
            for job in jobs:
                if job.get("id") == job_id:
                    job.update({k: body[k] for k in body if k != "id"})
                    write_json(CRON_PATH, jobs)
                    return self.send_json({"status": "updated", "job": job})
            return self.send_json({"error": "Job not found"}, 404)

        return self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")

        # DELETE /cron/<id>
        if len(parts) == 2 and parts[0] == "cron":
            job_id = int(parts[1])
            jobs = read_json(CRON_PATH, [])
            new_jobs = [j for j in jobs if j.get("id") != job_id]
            if len(new_jobs) == len(jobs):
                return self.send_json({"error": "Job not found"}, 404)
            write_json(CRON_PATH, new_jobs)
            return self.send_json({"status": "deleted"})

        return self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")

        # PATCH /cron/<id>/toggle
        if len(parts) == 3 and parts[0] == "cron" and parts[2] == "toggle":
            job_id = int(parts[1])
            jobs = read_json(CRON_PATH, [])
            for job in jobs:
                if job.get("id") == job_id:
                    job["enabled"] = not job.get("enabled", True)
                    write_json(CRON_PATH, jobs)
                    return self.send_json({"status": "toggled", "enabled": job["enabled"]})
            return self.send_json({"error": "Job not found"}, 404)

        return self.send_json({"error": "Not found"}, 404)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(WORKSPACE, exist_ok=True)
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    os.makedirs(INVOICES_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOGS_PATH), exist_ok=True)
    os.makedirs(os.path.join(WORKSPACE, "cron"), exist_ok=True)

    # Initialize cron if missing
    if not os.path.exists(CRON_PATH):
        write_json(CRON_PATH, [])

    # Start log watcher thread
    threading.Thread(target=watch_log, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"API bridge running on http://localhost:{PORT}")
    print(f"Workspace : {WORKSPACE}")
    print(f"Bujji URL : {BUJJI_URL}")
    print(f"Logs path : {LOGS_PATH}")
    print()
    print("Endpoints:")
    print(f"  POST  http://localhost:{PORT}/run          — Trigger agent")
    print(f"  POST  http://localhost:{PORT}/upload       — Upload invoice PDF")
    print(f"  GET   http://localhost:{PORT}/logs/stream  — SSE live log stream")
    print(f"  GET   http://localhost:{PORT}/logs         — Last 100 log lines")
    print(f"  GET   http://localhost:{PORT}/outputs      — List output files")
    print(f"  GET   http://localhost:{PORT}/status       — Agent status")
    print(f"  GET   http://localhost:{PORT}/cron         — List cron jobs")
    print(f"  POST  http://localhost:{PORT}/cron         — Create cron job")
    print(f"  PUT   http://localhost:{PORT}/cron/<id>    — Update cron job")
    print(f"  PATCH http://localhost:{PORT}/cron/<id>/toggle — Enable/disable")
    print(f"  DELETE http://localhost:{PORT}/cron/<id>   — Delete cron job")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")