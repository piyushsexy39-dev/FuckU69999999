import os
import sys
import time
import json
import fcntl
import signal
import socket
import threading
import subprocess
import http.server
import socketserver

PORT = int(os.getenv("PORT", "3000"))
HOST = os.getenv("HOST", "127.0.0.1")
START_TIME = time.time()
APPLET_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = APPLET_DIR
SERVER_LOCK_FILE = "/tmp/gullycric_server.lock"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ultimate Cricket Bot - Online</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background: #090d16; color: #f8fafc; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 24px; }
    .card { background: #131b2e; border: 1px solid #1e293b; border-radius: 20px; padding: 32px; max-width: 580px; width: 100%; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
    .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
    .title { font-size: 22px; font-weight: 700; color: #38bdf8; display: flex; align-items: center; gap: 10px; }
    .badge { background: #10b981; color: #ffffff; padding: 6px 14px; border-radius: 9999px; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: inline-flex; align-items: center; gap: 6px; }
    .pulse { width: 8px; height: 8px; background: #ffffff; border-radius: 50%; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.8); } 100% { opacity: 1; transform: scale(1); } }
    .desc { color: #94a3b8; font-size: 15px; line-height: 1.6; margin-bottom: 20px; }
    .btn { display: block; text-align: center; background: #0284c7; color: white; text-decoration: none; font-weight: 700; font-size: 16px; padding: 14px 24px; border-radius: 12px; transition: background 0.2s; margin-bottom: 24px; }
    .btn:hover { background: #0369a1; }
    .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }
    .stat-box { background: #1e293b; border-radius: 12px; padding: 14px; border: 1px solid #334155; }
    .stat-label { font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 600; }
    .stat-value { font-size: 16px; font-weight: 700; color: #e2e8f0; margin-top: 4px; }
    .commands { background: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; }
    .commands-title { font-size: 13px; font-weight: 700; color: #cbd5e1; margin-bottom: 10px; text-transform: uppercase; }
    .cmd-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; color: #94a3b8; }
    .cmd-grid code { color: #38bdf8; font-weight: 600; }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="title">🏏 Ultimate Cricket Bot</div>
      <div class="badge"><span class="pulse"></span> ONLINE</div>
    </div>
    <p class="desc">
      Your Telegram Cricket Bot is <strong>alive, actively polling, and handling games</strong> in real time!
    </p>
    <a href="https://t.me/UltimateCricket_BOT" target="_blank" class="btn">🚀 Open @UltimateCricket_BOT in Telegram</a>
    <div class="stats">
      <div class="stat-box">
        <div class="stat-label">Bot Handle</div>
        <div class="stat-value">@UltimateCricket_BOT</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Status</div>
        <div class="stat-value" style="color: #34d399;">Active Polling 200 OK</div>
      </div>
    </div>
    <div class="commands">
      <div class="commands-title">Available Commands on Telegram:</div>
      <div class="cmd-grid">
        <div><code>/start</code> - Main menu</div>
        <div><code>/solo</code> - Quick solo match</div>
        <div><code>/team</code> - Team 5v5 match</div>
        <div><code>/auction</code> - Player auction</div>
        <div><code>/tournament</code> - Ultimate Cup</div>
        <div><code>/market</code> - Gear & perks</div>
        <div><code>/stats</code> - Player profile</div>
        <div><code>/help</code> - Gameplay guide</div>
      </div>
    </div>
  </div>
</body>
</html>
"""

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/health", "/ping", "/api/health", "/status"):
            alive = active_bot_proc is not None and active_bot_proc.poll() is None
            self.send_response(200 if alive else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "online" if alive else "bot_down",
                "bot": "@UltimateCricket_BOT",
                "uptime_seconds": int(time.time() - START_TIME)
            }).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

    def log_message(self, format, *args):
        return

class ReusableServer(socketserver.TCPServer):
    allow_reuse_address = True
    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except Exception:
                pass
        super().server_bind()

def run_http_server():
    for attempt in range(15):
        try:
            with ReusableServer((HOST, PORT), HealthHandler) as httpd:
                print(f"[Server] Health/Dev server listening on {HOST}:{PORT}", flush=True)
                httpd.serve_forever()
                break
        except Exception as e:
            print(f"[Server] Port {PORT} busy, retrying in 1s... ({e})", flush=True)
            time.sleep(1)

def ensure_single_server():
    """Ensures only ONE server.py runs across the entire system."""
    global _server_lock_fd
    _server_lock_fd = open(SERVER_LOCK_FILE, "a+")
    
    # Try non-blocking lock
    try:
        fcntl.flock(_server_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, IOError):
        # Read old server PID and terminate it
        try:
            _server_lock_fd.seek(0)
            old_pid_str = _server_lock_fd.read().strip()
            if old_pid_str and old_pid_str.isdigit():
                old_pid = int(old_pid_str)
                if old_pid != os.getpid():
                    print(f"[Server] Found existing server PID {old_pid}, terminating to take over...", flush=True)
                    try:
                        os.kill(old_pid, signal.SIGTERM)
                        time.sleep(0.5)
                        os.kill(old_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        except Exception as ex:
            print(f"[Server] Note during old PID termination: {ex}", flush=True)

        # Wait and acquire lock
        for _ in range(5):
            try:
                fcntl.flock(_server_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, IOError):
                time.sleep(0.5)
        else:
            fcntl.flock(_server_lock_fd, fcntl.LOCK_EX)

    # Write current PID
    _server_lock_fd.seek(0)
    _server_lock_fd.truncate()
    _server_lock_fd.write(str(os.getpid()))
    _server_lock_fd.flush()
    print(f"[Server] Acquired exclusive server lock for PID {os.getpid()}.", flush=True)

active_bot_proc = None

def cleanup_children(signum=None, frame=None):
    global active_bot_proc
    if active_bot_proc and active_bot_proc.poll() is None:
        try:
            active_bot_proc.terminate()
            active_bot_proc.wait(timeout=2)
        except Exception:
            try:
                active_bot_proc.kill()
            except Exception:
                pass
    sys.exit(0)

def run_bot_supervisor():
    global active_bot_proc
    env = os.environ.copy()
    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        try:
            from dotenv import dotenv_values
            vals = dotenv_values(os.path.join(APPLET_DIR, ".env"))
            token = vals.get("BOT_TOKEN") or vals.get("TELEGRAM_BOT_TOKEN") or ""
        except Exception:
            pass
    if not token:
        print("[Server] BOT_TOKEN is not set. Put it in .env (see .env.example).", flush=True)
        sys.exit(1)
    env["BOT_TOKEN"] = token
    env["TELEGRAM_BOT_TOKEN"] = token
    env["DATABASE_URL"] = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{os.path.join(APPLET_DIR, 'gullycric.db')}")
    env["PYTHONPATH"] = BASE_DIR

    delay = 3
    while True:
        started = time.time()
        try:
            print("[Supervisor] Spawning single bot instance gullycric.src.main...", flush=True)
            active_bot_proc = subprocess.Popen(
                [sys.executable, "-m", "gullycric.src.main"],
                env=env,
                cwd=APPLET_DIR
            )
            ret = active_bot_proc.wait()
            print(f"[Supervisor] Bot process exited with code {ret}.", flush=True)
        except Exception as e:
            print(f"[Supervisor] Exception in supervisor: {e}", flush=True)
        # healthy run (>60s) resets backoff; rapid crashes back off up to 60s
        delay = 3 if time.time() - started > 60 else min(delay * 2, 60)
        print(f"[Supervisor] Restarting in {delay}s...", flush=True)
        time.sleep(delay)

def main():
    print("=== Ultimate Cricket Bot Server Initializing ===", flush=True)
    ensure_single_server()

    signal.signal(signal.SIGINT, cleanup_children)
    signal.signal(signal.SIGTERM, cleanup_children)

    # NOTE: package files already live in gullycric/src; the old symlink step
    # overwrote gullycric/src/main.py with the root wrapper (circular import).
    os.makedirs(os.path.join(BASE_DIR, "gullycric", "assets", "media"), exist_ok=True)

    # Start HTTP dashboard on port 3000
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    time.sleep(0.5)

    # Run single bot supervisor
    run_bot_supervisor()

if __name__ == "__main__":
    main()
