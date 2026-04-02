"""
Dedicated logger for WebSocket subscription and snapshot lifecycle events.

Writes to logs/ws_snapshot.log at DEBUG level regardless of root logger config.
Import and use `ws_log` anywhere you need WS/snapshot visibility.
"""
import logging
import os

_LOG_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'logs', 'ws_snapshot.log'
)
_LOG_PATH = os.path.normpath(_LOG_PATH)

ws_log = logging.getLogger('ws_snapshot')
ws_log.setLevel(logging.DEBUG)
ws_log.propagate = False  # don't bubble up to root (which may suppress)

if not ws_log.handlers:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    _fh = logging.FileHandler(_LOG_PATH, encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    ws_log.addHandler(_fh)
