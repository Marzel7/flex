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
