from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config/supervisor/watchtower_frozen_shadow.template.conf"


def test_frozen_shadow_runtime_template_declares_only_isolated_default_off_programs(tmp_path):
    rendered = TEMPLATE.read_text().replace("__WATCHTOWER_ROOT__", str(tmp_path / "runtime")) \
        .replace("__WATCHTOWER_FINAL_SHA__", "daca3e8d9d50a272b0e6ff56aee9a640aab0ebfe") \
        .replace("__WATCHTOWER_OFFSET_AUDIT_LEDGER_PATH__", str(tmp_path / "ledger.json")) \
        .replace("__CANONICAL_DB_PATH__", str(tmp_path / "canonical.db")) \
        .replace("__OPERATIONS_DB_PATH__", str(tmp_path / "operations.db")) \
        .replace("__MONITOR_QUEUE_PATH__", str(tmp_path / "queue")) \
        .replace("__WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH__", str(tmp_path / "shadow.json")) \
        .replace("__PYTHON_BIN__", "/usr/bin/python3") \
        .replace("__LOG_ROOT__", str(tmp_path / "logs"))
    assert "__" not in rendered
    assert rendered.count("[program:watchtower_api]") == 1
    assert rendered.count("[program:operation_monitor_worker]") == 1
    assert rendered.count("autostart=false") == 2
    assert rendered.count("autorestart=false") == 2
    assert "WATCHTOWER_SHADOW_EVALUATION_ENABLED=\"0\"" in rendered
    assert "WATCHTOWER_SHADOW_CAPTURE_ENABLED=\"0\"" in rendered
    assert "WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH=" in rendered
    assert "src.core.main:app" in rendered
    assert "src.ops.operation_monitor_service" in rendered
    assert "HELIUS_RPC_URL" not in rendered
    assert "api-key=" not in rendered
