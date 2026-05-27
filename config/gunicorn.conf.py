"""
Gunicorn configuration for WATCHTOWER Flask app.
Replaces Flask dev server for production-grade overnight operation.
"""
import multiprocessing
import os

# Binding
bind = "0.0.0.0:5002"

# Workers — sync+threaded is safer with our daemon-thread-heavy architecture.
# gevent monkey-patches can deadlock with threading.Thread I/O.
worker_class = "gthread"
workers = 2
threads = 4

# Recycling — prevents slow memory leaks over multi-day runs
max_requests = 2000
max_requests_jitter = 200

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
