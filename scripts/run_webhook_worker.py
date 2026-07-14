#!/usr/bin/env python3
"""
Standalone entry point for the webhook queue worker.

Extracted from Gunicorn (A2.6): run_worker previously ran as a daemon thread
inside init_webhook_system(). It is now a standalone supervised process.

Gunicorn continues to register webhook routes. This process owns the queue.

Usage:
    python scripts/run_webhook_worker.py
    python -m scripts.run_webhook_worker
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.webhook_worker import run_worker

if __name__ == "__main__":
    run_worker()
