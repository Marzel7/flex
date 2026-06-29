"""
Gunicorn configuration for WATCHTOWER Flask app.
Replaces Flask dev server for production-grade overnight operation.
"""
import multiprocessing
import os

# Binding
bind = "0.0.0.0:5002"

recovery_mode = os.environ.get("FLEX_UI_RECOVERY_MODE", "0").lower() in {"1", "true", "yes"}

# Workers — sync+threaded is safer with our daemon-thread-heavy architecture.
# gevent monkey-patches can deadlock with threading.Thread I/O.
worker_class = "gthread"
workers = 1
# Dashboard pages fan out 11-22 concurrent /api fetches on load. With only 8
# threads the page's own requests starved each other — one transiently-slow
# handler queued the rest, manifesting as "loading very slow". These handlers are
# I/O-bound (SQLite reads release the GIL), so more threads is the right lever.
threads = 4 if recovery_mode else 24

# Recycling — prevents slow memory leaks over multi-day runs
max_requests = 500
max_requests_jitter = 50

# Timeouts
timeout = 120           # worker killed if no response in 120s
keepalive = 5
graceful_timeout = 10   # reduced — orphan workers linger if this is too long

# Logging
accesslog = os.path.join(os.path.dirname(__file__), "../logs/gunicorn_access.log")
errorlog  = os.path.join(os.path.dirname(__file__), "../logs/gunicorn_error.log")
loglevel  = "warning"
access_log_format = '%(t)s %(s)s %(m)s %(U)s %(D)sµs'

# Process naming
proc_name = "watchtower_api"

# Preload app so workers share memory for read-heavy routes
preload_app = False   # keep False — our app spawns threads at import time

# Worker tmp directory (avoids /tmp contention on macOS)
worker_tmp_dir = "/tmp"

# On worker exit, log it
def worker_exit(server, worker):
    server.log.info(f"[GUNICORN] Worker {worker.pid} exited")

def on_starting(server):
    server.log.info("[GUNICORN] WATCHTOWER API starting")

def on_reload(server):
    server.log.info("[GUNICORN] WATCHTOWER API reloading")
