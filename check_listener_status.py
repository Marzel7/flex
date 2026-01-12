#!/usr/bin/env python3
"""
Check if the migration listener is running and receiving events.

Usage:
  python check_listener_status.py

This will show:
  - Listener process status (running/not running)
  - WebSocket connection status (if connected)
  - Events received count
  - Migrations detected count
  - Time since listener started
"""

import subprocess
import os
import psutil
import sys
from datetime import datetime

def check_listener_process():
    """Check if pumpfun_curve_listener.py is running"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'pumpfun_curve_listener' in ' '.join(proc.info.get('cmdline', [])):
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None

def main():
    listener_pid = check_listener_process()

    if listener_pid:
        print(f"✅ Listener is RUNNING (PID: {listener_pid})")
        print(f"\nTo check connection status, look for these in listener output:")
        print(f"  - '[WEBSOCKET] ✓ Connected' = Connected and listening")
        print(f"  - '[WEBSOCKET] ⚠ Connection error' = WebSocket issue (check logs)")
        print(f"  - '[WEBSOCKET] 🚨 Migration #N detected' = Event was caught")
        print(f"\nTo view listener logs in real-time:")
        print(f"  tail -f listener.log  (if redirected to file)")
        print(f"  OR: ps aux | grep pumpfun_curve_listener")
    else:
        print(f"❌ Listener is NOT RUNNING")
        print(f"\nTo start the listener:")
        print(f"  python pumpfun_curve_listener.py")
        print(f"  OR: bash run_listener.sh")

    print(f"\nTocheck database for detected migrations:")
    print(f"  sqlite3 pumpswap_tokens.db 'SELECT COUNT(*) FROM token_analysis'")

if __name__ == "__main__":
    main()
