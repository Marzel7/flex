"""The Scheduler's ARM reconcile must not fork the ops DB on source cutover."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _probe(tmp_path: Path, *, primary: bool) -> Path:
    selected = tmp_path / "shared-ops.db"
    fallback = tmp_path / "fallback-ops.db"
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(ROOT),
        PYTHONDONTWRITEBYTECODE="1",
        DB_PATH=str(tmp_path / "shared-live.db"),
        FLEX_DB_PATH=str(tmp_path / "shared-live.db"),
        WT_OPS_DB_PATH=str(fallback),
        OPS_SCHEDULER_LOCK=str(tmp_path / "shared-scheduler.lock"),
    )
    if primary:
        env["OPS_V2_DB_PATH"] = str(selected)
    else:
        env.pop("OPS_V2_DB_PATH", None)
        selected = fallback

    local_db = ROOT / "database" / "wt_ops_v2.db"
    local_guard = ROOT / "database" / "wt_ops_v2.db.write.lock.owner.guard"
    assert not local_db.exists()
    assert not local_guard.exists()

    code = """
import os
from src.core import (
    attribution_evidence,
    operation_armed,
    operation_merge_ledger,
    treasury_bank,
    watchtower_attribution,
    wrap_close_detector,
)
selected = os.environ.get('OPS_V2_DB_PATH', os.environ['WT_OPS_DB_PATH'])
for module in (
    attribution_evidence, operation_armed, operation_merge_ledger,
    treasury_bank, watchtower_attribution, wrap_close_detector,
):
    assert module.OPS_DB_PATH == selected, module.__name__
if os.environ.get('OPS_V2_DB_PATH'):
    from src.core import operation_scheduler, operation_store_v2
    assert operation_armed.OPS_DB_PATH == operation_scheduler.OPS_DB_PATH == operation_store_v2.OPS_DB_PATH
    from src.utils.db_locking import db_connect
    conn = db_connect(selected, timeout=5)
    operation_store_v2.ensure_schema(conn)
    operation_scheduler._ensure_fwd_schema(conn)
    operation_scheduler.ensure_run_log(conn)
    conn.close()
    operation_scheduler.operation_forward_monitor = lambda **kwargs: {
        'rpc_calls': 0, 'new_children': 1, 'new_candidates': 0,
    }
    assert operation_scheduler.run_forward_job(quiet=True)['status'] == 'OK'
    operation_scheduler._flush_sched_state()
assert operation_armed.reconcile_armed() == {
    'fired': 0, 'expired': 0, 'enroll_retried': 0, 'still_armed': 0,
}
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True)

    assert selected.exists()
    with sqlite3.connect(f"file:{selected}?mode=ro", uri=True) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_ops_v2_armed'"
        ).fetchone() == (1,)
    assert not local_db.exists()
    assert not local_guard.exists()
    if primary:
        assert not fallback.exists()
    return selected


def test_scheduler_armed_reconcile_uses_explicit_ops_authority(tmp_path):
    assert _probe(tmp_path, primary=True).name == "shared-ops.db"


def test_scheduler_armed_reconcile_accepts_shared_ops_alias(tmp_path):
    assert _probe(tmp_path, primary=False).name == "fallback-ops.db"
