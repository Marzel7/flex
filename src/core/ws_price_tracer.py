"""
ws_price_tracer.py — per-token lifecycle trace log for WS subscription + first-price diagnosis.

Logs every meaningful stage from migration detection → pool register → WS subscribe
→ reserve update → first on-chain price, all keyed by mint.

Log file: logs/ws_price_trace.log
Format:  TIMESTAMP  STAGE  mint[:16]  message

Stages (in expected order):
  MIGRATION_DETECTED        listener detected a new pump.fun migration
  POOL_REGISTERED           pool + vaults written to DB
  WS_SUBSCRIBE_QUEUED       new vault accounts enqueued for incremental WS subscribe
  WS_SUBSCRIBE_SENT         accountSubscribe RPC sent for vault
  WS_SUBSCRIBE_CONFIRMED    server returned subscription ID
  RESERVE_UPDATE            accountNotification decoded a reserve balance
  POOL_STATE_READY          both base+quote reserves now set — pool usable for pricing
  FAST_LANE_PRICE           fast-lane thread got a valid price (onchain or fallback)
  FAST_LANE_TIMEOUT         fast-lane gave up after 60s with no price
  ONCHAIN_FAILED            _fetch_single_price returned onchain_failed
  FIRST_SNAPSHOT            first price snapshot written to DB

Import trace() and call it at each stage.  mint is always the full mint string.
"""
import logging
import os
import time

_LOG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'logs', 'ws_price_trace.log')
)

_tracer = logging.getLogger('ws_price_tracer')
_tracer.setLevel(logging.DEBUG)
_tracer.propagate = False

if not _tracer.handlers:
    import logging.handlers as _lh
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    _fh = _lh.RotatingFileHandler(
        _LOG_PATH, maxBytes=50 * 1024 * 1024, backupCount=2, encoding='utf-8'
    )
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        '%(asctime)s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    _tracer.addHandler(_fh)


def trace(stage: str, mint: str, detail: str = '') -> None:
    """Write one trace line.  stage is a short ALL_CAPS token, mint is full pubkey."""
    tag = mint[:20] if mint else 'UNKNOWN'
    msg = f"{stage:<28}  {tag}  {detail}" if detail else f"{stage:<28}  {tag}"
    _tracer.info(msg)
