"""Render-only DEV037 qualification. No application import or database access.

All API responses are explicitly unavailable fixtures, not live evidence.
Run only on a separate loopback port. Does not replace src.dev_runtime.
"""
import sqlite3
import socket
from pathlib import Path
from flask import Flask, jsonify, render_template

ROOT = Path(__file__).resolve().parents[1]

def forbidden_database(*args, **kwargs):
    raise RuntimeError("DEV037 render fixture forbids all database connections")

sqlite3.connect = forbidden_database
original_connect = socket.socket.connect
def loopback_only(sock, address):
    if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("DEV037 render fixture forbids outbound network")
    return original_connect(sock, address)
socket.socket.connect = loopback_only
app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.config["DEV_RUNTIME"] = True

@app.after_request
def fixture_label(response):
    if response.mimetype == 'text/html':
        response.set_data(response.get_data().replace(b'<body>', b'<body><p style="background:#7c2d12;color:white;padding:12px">DEV037 SYNTHETIC PRESENTATION FIXTURE - NOT LIVE TOKEN EVIDENCE</p>', 1))
    return response

@app.get('/api/operations/live-monitor')
def monitor_fixture():
    rows = [dict(operation_id=operation, mint='FIXTURE_'+operation,
                 token_symbol='FIXTURE '+operation.upper(), cohort_class='PROSPECTIVE_MONITOR_COHORT',
                 monitor_state='MONITORING_ACTIVE', assignment_timestamp=100,
                 entry_mc_usd=100, running_peak_mc_usd=200, latest_mc_usd=150,
                 current_multiple=1.5, peak_exactness='CANDLE_HIGH')
            for operation in ['watchtower', 'byzantine']]
    return jsonify(qualification_fixture=True, mode='MONITOR', now=200, rows=rows,
                   operations={}, summary=dict(active=2, waiting=0, completed_today=0, calls_today=0),
                   storage=dict(bytes=0), worker_health=dict(status='UNKNOWN'),
                   queue_health=dict(depth=0), provider_health=dict(status='UNKNOWN'))

@app.get("/")
@app.get("/intelligence/operators")
def registry():
    return render_template("operators_index.html", active_page="operators")

@app.get("/operations/live-monitor")
def monitor():
    return render_template("operations_live_monitor.html", active_page="live_monitor")

@app.get("/ops-os")
def shell():
    return render_template("ops_shell_index.html", active_page="ops_os")

@app.get("/healthz")
def health():
    return jsonify(healthy=True, qualification_fixture=True)

@app.get("/api/<path:unused>")
def unavailable(unused):
    return jsonify(status="UNAVAILABLE", error="DEV037 render-only fixture; no live backend evidence"), 503

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8102, use_reloader=False, threaded=True)
