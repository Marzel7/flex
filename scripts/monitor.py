#!/usr/bin/env python3
"""
Flex App Monitor
================
Polls the app health endpoint, watches log files for error patterns,
and prints classified alerts when outages or degradation are detected.

Usage:
    python scripts/monitor.py [--interval 30]

Output format:
    [HH:MM:SS] STATUS | latency | detail
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from collections import deque, defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL    = "http://localhost:5002"
HEALTH_URL  = f"{BASE_URL}/api/price/health"
DB_PATH     = "database/flex_complete_database.db"

LOG_FILES = {
    "main":     "logs/main.log",
    "ws":       "logs/ws_snapshot.log",
    "ws_trace": "logs/ws_price_trace.log",
    "flask":    "flask.log",
}

# Patterns that indicate a known root cause (checked in order — first match wins)
ERROR_PATTERNS = [
    # (regex, severity, root_cause_label)
    (r"database is locked",              "WARN",  "DB_LOCK: sqlite contention — write timeout too short or excessive concurrency"),
    (r"DB_CONNECT_SLOW.*caller=",        "WARN",  "DB_SLOW: slow lock acquisition — caller logged above shows contention source"),
    (r"DB_CONNECT_FAIL.*caller=",        "CRIT",  "DB_FAIL: connection failed — caller logged above"),
    (r"THREAD CRASHED",                  "CRIT",  "THREAD_CRASH: price_worker thread died — check main.log for traceback"),
    (r"Address already in use",          "CRIT",  "PORT_CONFLICT: port 5002 already bound — stale process from prior run"),
    (r"1001 going away",                 "WARN",  "WS_GOING_AWAY: Helius WS closed with 1001 — should reconnect automatically"),
    (r"ConnectionClosed",                "WARN",  "WS_CLOSED: WebSocket connection closed — reconnect in progress"),
    (r"keepalive ping timeout",          "WARN",  "WS_TIMEOUT: WebSocket keepalive failed — network hiccup"),
    (r"\[CREATOR\]\[DEBUG\]",            "WARN",  "DEBUG_FLOOD: [CREATOR][DEBUG] prints active — will peg CPU on non-bonding-curve txs"),
    (r"97\.\d% CPU|9[89]\.\d% CPU",     "CRIT",  "CPU_PEEL: process at near-100% CPU — likely log flood or tight loop"),
    (r"SnapshotRetentionCleanup",        "INFO",  "RETENTION: startup cleanup running — brief DB lock spike expected"),
    (r"bootstrap.*timeout|join.*timeout","WARN",  "BOOTSTRAP_SLOW: pool bootstrap taking longer than expected"),
    (r"Error in price stream|SSE.*error","WARN",  "SSE_ERROR: price-stream SSE error — clients will reconnect"),
    (r"memory.*error|MemoryError",       "CRIT",  "OOM: memory error — process may be about to die"),
    (r"RecursionError",                  "CRIT",  "RECURSION: infinite recursion detected"),
    (r"Traceback \(most recent",         "CRIT",  "EXCEPTION: unhandled exception in log — check traceback"),
]

COMPILED = [(re.compile(p, re.IGNORECASE), sev, label) for p, sev, label in ERROR_PATTERNS]

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class Monitor:
    def __init__(self, interval: int):
        self.interval       = interval
        self.log_offsets    = {}          # filename -> byte offset
        self.outage_start   = None        # timestamp when Flask first became unreachable
        self.last_status    = "UNKNOWN"
        self.latency_window = deque(maxlen=10)   # last 10 response times (ms)
        self.error_counts   = defaultdict(int)   # label -> count since last clear
        self.last_errors    = {}          # label -> last seen timestamp
        self.cycle          = 0

    # -----------------------------------------------------------------------
    # HTTP health check
    # -----------------------------------------------------------------------

    def check_http(self):
        """Returns (status, latency_ms, detail_dict)."""
        t0 = time.time()
        try:
            req = urllib.request.Request(HEALTH_URL, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                latency = int((time.time() - t0) * 1000)
                body    = resp.read().decode()
                data    = json.loads(body) if body else {}
                return "OK", latency, data
        except urllib.error.HTTPError as e:
            latency = int((time.time() - t0) * 1000)
            return f"HTTP_{e.code}", latency, {"error": str(e)}
        except urllib.error.URLError as e:
            latency = int((time.time() - t0) * 1000)
            reason  = str(e.reason) if hasattr(e, "reason") else str(e)
            if "Connection refused" in reason:
                return "DOWN", latency, {"error": "Connection refused — Flask process not running"}
            return "UNREACHABLE", latency, {"error": reason}
        except Exception as e:
            latency = int((time.time() - t0) * 1000)
            return "ERROR", latency, {"error": str(e)}

    # -----------------------------------------------------------------------
    # Log scanning
    # -----------------------------------------------------------------------

    def scan_logs(self):
        """Read new lines from each log file, return list of (file, line, severity, label)."""
        findings = []
        for name, path in LOG_FILES.items():
            if not os.path.exists(path):
                continue
            offset = self.log_offsets.get(name, None)
            try:
                with open(path, "rb") as f:
                    size = f.seek(0, 2)
                    if offset is None:
                        # First run — start at end so we don't replay history
                        self.log_offsets[name] = size
                        continue
                    if size < offset:
                        # File was rotated/truncated
                        offset = 0
                    f.seek(offset)
                    new_bytes = f.read()
                    self.log_offsets[name] = f.tell()

                all_lines = new_bytes.decode("utf-8", errors="replace").splitlines()
                i = 0
                while i < len(all_lines):
                    line = all_lines[i].strip()
                    i += 1
                    if not line:
                        continue

                    # Collect full traceback block so we can inspect the exception type
                    if "Traceback (most recent call last)" in line:
                        block = [line]
                        while i < len(all_lines):
                            next_line = all_lines[i].strip()
                            block.append(next_line)
                            i += 1
                            # Exception line is the first non-indented line after the traceback
                            if next_line and not next_line.startswith(" ") and not next_line.startswith("File "):
                                break
                        exception_line = block[-1] if block else ""
                        # Known harmless — SSE browser disconnect
                        if "GeneratorExit" in exception_line or "generator ignored GeneratorExit" in exception_line:
                            continue
                        # Known harmless — client closed connection mid-stream
                        if "BrokenPipeError" in exception_line or "ConnectionResetError" in exception_line:
                            continue
                        # Known harmless — Werkzeug SSE client disconnect (connection_dropped)
                        if "connection_dropped" in " ".join(block):
                            continue
                        full = " | ".join(block[-3:])  # last 3 lines of traceback
                        findings.append((name, full[:200], "CRIT", "EXCEPTION: unhandled exception — " + exception_line[:80]))
                        continue

                    for pattern, sev, label in COMPILED:
                        if pattern.search(line):
                            findings.append((name, line[:200], sev, label))
                            break  # first match only per line
            except Exception:
                pass
        return findings

    # -----------------------------------------------------------------------
    # Formatting helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def ts():
        return datetime.now().strftime("%H:%M:%S")

    @staticmethod
    def color(text, code):
        # Only colorize if stdout is a terminal
        if sys.stdout.isatty():
            return f"\033[{code}m{text}\033[0m"
        return text

    def fmt_status(self, status):
        if status == "OK":
            return self.color("OK       ", "32")       # green
        if status.startswith("HTTP_5"):
            return self.color(f"{status:<9}", "33")    # yellow
        return self.color(f"{status:<9}", "31")        # red

    def fmt_sev(self, sev):
        if sev == "INFO":  return self.color("INFO", "36")
        if sev == "WARN":  return self.color("WARN", "33")
        if sev == "CRIT":  return self.color("CRIT", "31;1")
        return sev

    # -----------------------------------------------------------------------
    # Health data extraction
    # -----------------------------------------------------------------------

    def extract_health_summary(self, data: dict) -> str:
        """Pull key metrics from health response for one-line display."""
        parts = []
        try:
            ws = data.get("websocket") or data.get("ws") or {}
            if isinstance(ws, dict):
                connected = ws.get("connected") or ws.get("is_connected")
                stale     = ws.get("is_stale") or ws.get("stale")
                if connected is False:
                    parts.append(self.color("WS:DOWN", "31"))
                elif stale:
                    parts.append(self.color("WS:STALE", "33"))
                else:
                    parts.append(self.color("WS:OK", "32"))

            pool = data.get("pool") or data.get("pool_state") or {}
            if isinstance(pool, dict):
                n = pool.get("total") or pool.get("pool_count") or pool.get("mints_tracked")
                if n is not None:
                    parts.append(f"pools:{n}")

            snaps = data.get("snapshots") or data.get("snapshot") or {}
            if isinstance(snaps, dict):
                rate = snaps.get("per_minute") or snaps.get("rate")
                if rate is not None:
                    parts.append(f"snaps/min:{rate:.1f}" if isinstance(rate, float) else f"snaps/min:{rate}")

            worker = data.get("worker") or {}
            if isinstance(worker, dict):
                cycles = worker.get("cycles")
                if cycles is not None:
                    parts.append(f"cycles:{cycles}")
        except Exception:
            pass
        return "  ".join(parts) if parts else ""

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def run(self):
        print(f"[{self.ts()}] Flex monitor started  interval={self.interval}s  health={HEALTH_URL}")
        print(f"[{self.ts()}] Watching logs: {', '.join(k for k, v in LOG_FILES.items() if os.path.exists(v))}")
        print()

        while True:
            self.cycle += 1
            now     = time.time()
            status, latency, data = self.check_http()
            self.latency_window.append(latency)
            findings = self.scan_logs()

            # --- outage tracking ---
            if status not in ("OK",) and not status.startswith("HTTP_2"):
                if self.outage_start is None:
                    self.outage_start = now
                outage_secs = int(now - self.outage_start)
                outage_str  = f"  {self.color(f'OUTAGE {outage_secs}s', '31;1')}"
            else:
                if self.outage_start is not None:
                    duration = int(now - self.outage_start)
                    print(f"[{self.ts()}] {self.color(f'RECOVERED after {duration}s', '32;1')}")
                self.outage_start = None
                outage_str = ""

            # --- latency trend ---
            avg_latency = int(sum(self.latency_window) / len(self.latency_window))
            lat_color   = "32" if avg_latency < 500 else ("33" if avg_latency < 2000 else "31")
            lat_str     = self.color(f"{latency}ms (avg {avg_latency}ms)", lat_color)

            # --- health summary ---
            summary = self.extract_health_summary(data) if status == "OK" else (data.get("error", "") if isinstance(data, dict) else "")

            # --- main status line ---
            print(f"[{self.ts()}] {self.fmt_status(status)} {lat_str}{outage_str}  {summary}")

            # --- log findings ---
            for fname, line, sev, label in findings:
                self.error_counts[label] += 1
                self.last_errors[label]   = now
                short_line = line[:120] + ("…" if len(line) > 120 else "")
                print(f"  {self.fmt_sev(sev)} [{fname}] {label}")
                print(f"        → {short_line}")

            # --- slow response warning ---
            if status == "OK" and latency > 3000:
                print(f"  {self.fmt_sev('WARN')} SLOW_RESPONSE: Flask took {latency}ms — may be blocking on DB or RPC")

            # --- periodic summary (every 10 cycles) ---
            if self.cycle % 10 == 0 and self.error_counts:
                print(f"  {self.color('--- Error summary (since start) ---', '36')}")
                for label, count in sorted(self.error_counts.items(), key=lambda x: -x[1]):
                    ago = int(now - self.last_errors[label])
                    print(f"    {count:>4}x  {label}  (last {ago}s ago)")

            self.last_status = status
            time.sleep(self.interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Flex app monitor")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default 30)")
    args = parser.parse_args()

    monitor = Monitor(interval=args.interval)
    try:
        monitor.run()
    except KeyboardInterrupt:
        print(f"\n[{monitor.ts()}] Monitor stopped.")
        if monitor.error_counts:
            print("\nTotal errors seen this session:")
            for label, count in sorted(monitor.error_counts.items(), key=lambda x: -x[1]):
                print(f"  {count:>4}x  {label}")


if __name__ == "__main__":
    main()
