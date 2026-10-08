from pathlib import Path
import json
import subprocess
import sys

from src.ops.dev023_hot_rpc_cache_maintenance import RuntimeRetentionConfig
from src.ops.dev023_hot_rpc_cache_schedule import ScheduledRetentionConfig, run_scheduled_retention_tick


def _config(tmp_path, *, enabled=True):
    return ScheduledRetentionConfig(
        enabled=enabled,
        tick=RuntimeRetentionConfig(False, str(tmp_path / "canonical.db"), 1, ""),
        lease_path=str(tmp_path / "retention.lease"), state_path=str(tmp_path / "retention.state"),
    )


def test_disabled_scheduler_does_not_call_tick_or_write_state(tmp_path):
    called = False
    def tick(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"status": "COMPLETE", "deleted": 0}
    assert run_scheduled_retention_tick(_config(tmp_path, enabled=False), tick_runner=tick) == {
        "status": "DISABLED", "deleted": 0, "alert": False
    }
    assert not called and not (tmp_path / "retention.state").exists()


def test_checked_in_schedule_entrypoint_is_disabled_without_opening_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/run_dev023_hot_rpc_cache_schedule.py"),
                             "--canonical-db-path", str(tmp_path / "missing.db")], cwd=root,
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "DISABLED", "deleted": 0, "alert": False}


def test_scheduler_reports_cap_pressure_and_bounded_metrics(tmp_path):
    result = run_scheduled_retention_tick(
        _config(tmp_path), tick_runner=lambda *_args, **_kwargs: {"status": "STOP_ROW_CAP", "deleted": 200}, now=lambda: 7
    )
    assert result["deleted"] == 200
    assert result["maintenance_lag"] == "CAPACITY_PRESSURE"
    assert result["remaining_expired_rows"] == "UNKNOWN_NO_EXPIRY_INDEX"
    assert (tmp_path / "retention.state").stat().st_size <= 4096


def test_overlap_skips_without_running_tick(tmp_path):
    from src.ops.storage_lock_safety import acquire_cleanup_lease
    with acquire_cleanup_lease(str(tmp_path / "retention.lease")):
        assert run_scheduled_retention_tick(_config(tmp_path), tick_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError())) == {
            "status": "SKIP_OVERLAP", "deleted": 0, "alert": False
        }


def test_repeated_failures_alert_and_success_resets_failures(tmp_path):
    config = _config(tmp_path)
    for _ in range(2):
        assert not run_scheduled_retention_tick(config, tick_runner=lambda *_a, **_k: {"status": "STOP_WAL_CEILING", "deleted": 0})["alert"]
    assert run_scheduled_retention_tick(config, tick_runner=lambda *_a, **_k: {"status": "STOP_WAL_CEILING", "deleted": 0})["alert"]
    assert not run_scheduled_retention_tick(config, tick_runner=lambda *_a, **_k: {"status": "COMPLETE", "deleted": 0})["alert"]
