"""Provider-free guard for the qualified Watchtower monitor topology.

This test deliberately assesses capabilities, not a historical list of
Supervisor stanzas.  A future replacement may satisfy the contract only when
it supplies the same explicit producer/consumer interfaces.
"""
from __future__ import annotations

import configparser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _programs(text: str) -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return {
        section.removeprefix("program:"): dict(parser[section])
        for section in parser.sections()
        if section.startswith("program:")
    }


def _enabled(program: dict[str, str]) -> bool:
    return program.get("autostart", "false").strip().lower() == "true"


def _worker_contract(program: dict[str, str] | None) -> str:
    if program is None:
        return "MISSING_OR_DISCONNECTED"
    command = program.get("command", "")
    environment = program.get("environment", "")
    launcher = "scripts/launch_watchtower_final.sh worker" in command
    required = (
        "src.ops.operation_monitor_service" in command or launcher,
        'OPERATIONS_MODE="MONITOR"' in environment,
        "WT_OPS_DB_PATH=" in environment,
        "OPERATION_MONITOR_QUEUE_PATH=" in environment,
    )
    if not all(required):
        return "MISSING_OR_DISCONNECTED"
    return "PRESENT_AND_REACHABLE" if _enabled(program) else "PRESENT_BUT_DISABLED"


def _bridge_contract(program: dict[str, str] | None) -> str:
    if program is None:
        return "MISSING_OR_DISCONNECTED"
    command = program.get("command", "")
    environment = program.get("environment", "")
    required = (
        "src.ops.operation_monitor_bridge_service" in command,
        "MONITOR_BRIDGE_SOURCE_DB=" in environment,
        "MONITOR_BRIDGE_MONITOR_DB=" in environment,
        "MONITOR_BRIDGE_QUEUE_PATH=" in environment,
    )
    if not all(required):
        return "MISSING_OR_DISCONNECTED"
    return "PRESENT_AND_REACHABLE" if _enabled(program) else "PRESENT_BUT_DISABLED"


def assess_monitoring_capabilities(text: str) -> dict[str, str]:
    """Classify only proved execution paths; names alone never imply equivalence."""
    programs = _programs(text)
    worker = _worker_contract(programs.get("operation_monitor_worker"))
    bridge = _bridge_contract(programs.get("operation_monitor_bridge"))
    helius = programs.get("watchtower_helius_monitor")
    return {
        "qualified_entry_to_live_admission": bridge,
        "lifecycle_polling_persistence_terminal_history": worker,
        # The Helius CLI writes usage snapshots; it is not a MonitorQueue
        # consumer nor an operation-fact lifecycle executor.
        "helius_monitor_replacement_equivalence": (
            "UNPROVEN" if helius is not None else "MISSING_OR_DISCONNECTED"
        ),
    }


def deployment_safe(matrix: dict[str, str]) -> bool:
    required = (
        "qualified_entry_to_live_admission",
        "lifecycle_polling_persistence_terminal_history",
    )
    return all(matrix[name] == "PRESENT_AND_REACHABLE" for name in required)


def test_qualified_source_contract_connects_admission_to_lifecycle_and_terminal_history():
    service = (ROOT / "src/ops/operation_monitor_service.py").read_text()
    worker = (ROOT / "src/ops/operation_monitor_worker.py").read_text()
    admission = (ROOT / "src/ops/monitor_live_admission.py").read_text()
    helius = (ROOT / "src/monitoring/helius_cli_monitor.py").read_text()

    for call in (
        "reconcile_watchtower_assignment_admissions(db_path, queue)",
        "worker.reconcile_retained_watchtower_facts()",
        "worker.reconcile_terminal_ath_jobs()",
        "worker.process_entry_reference_opening_once()",
        "worker.process_once()",
    ):
        assert call in service
    launcher = (ROOT / "scripts/launch_watchtower_final.sh").read_text()
    assert "def _activate_from_qualified_opening" in worker
    assert "def reconcile_terminal_ath_jobs" in worker
    assert "def consume_once(" in admission
    assert "queue.enqueue_after_assignment" in admission
    assert "Helius CLI-based Account Monitor" in helius
    assert "operation_monitor_facts" not in helius
    assert "MonitorQueue" not in helius
    assert "run_dev_005a_monitor.sh" in launcher
    assert "--dev-soak-selection" in launcher


def test_complete_enabled_monitor_topology_is_the_only_proved_operational_path():
    text = '''
[program:operation_monitor_worker]
command=python -m src.ops.operation_monitor_service
environment=OPERATIONS_MODE="MONITOR",WT_OPS_DB_PATH="/ops.db",OPERATION_MONITOR_QUEUE_PATH="/queue"
autostart=true

[program:operation_monitor_bridge]
command=python -m src.ops.operation_monitor_bridge_service
environment=MONITOR_BRIDGE_SOURCE_DB="/source.db",MONITOR_BRIDGE_MONITOR_DB="/ops.db",MONITOR_BRIDGE_QUEUE_PATH="/queue"
autostart=true
'''
    matrix = assess_monitoring_capabilities(text)
    assert matrix == {
        "qualified_entry_to_live_admission": "PRESENT_AND_REACHABLE",
        "lifecycle_polling_persistence_terminal_history": "PRESENT_AND_REACHABLE",
        "helius_monitor_replacement_equivalence": "MISSING_OR_DISCONNECTED",
    }
    assert deployment_safe(matrix) is True


def test_disabled_or_disconnected_topology_is_not_reported_operational():
    text = '''
[program:operation_monitor_worker]
command=python -m src.ops.operation_monitor_service
environment=OPERATIONS_MODE="MONITOR",WT_OPS_DB_PATH="/ops.db",OPERATION_MONITOR_QUEUE_PATH="/queue"
autostart=false

[program:operation_monitor_bridge]
command=python -m src.ops.operation_monitor_bridge_service
environment=MONITOR_BRIDGE_SOURCE_DB="/source.db",MONITOR_BRIDGE_MONITOR_DB="/ops.db",MONITOR_BRIDGE_QUEUE_PATH="/queue"
autostart=false
'''
    matrix = assess_monitoring_capabilities(text)
    assert set(matrix.values()) >= {"PRESENT_BUT_DISABLED", "MISSING_OR_DISCONNECTED"}
    assert deployment_safe(matrix) is False


def test_missing_queue_binding_fails_closed_even_when_program_names_remain():
    text = '''
[program:operation_monitor_worker]
command=python -m src.ops.operation_monitor_service
environment=OPERATIONS_MODE="MONITOR",WT_OPS_DB_PATH="/ops.db"
autostart=true

[program:operation_monitor_bridge]
command=python -m src.ops.operation_monitor_bridge_service
environment=MONITOR_BRIDGE_SOURCE_DB="/source.db",MONITOR_BRIDGE_MONITOR_DB="/ops.db"
autostart=true
'''
    matrix = assess_monitoring_capabilities(text)
    assert matrix["qualified_entry_to_live_admission"] == "MISSING_OR_DISCONNECTED"
    assert matrix["lifecycle_polling_persistence_terminal_history"] == "MISSING_OR_DISCONNECTED"
    assert deployment_safe(matrix) is False


def test_current_helius_stanza_cannot_mask_missing_monitor_pipeline():
    current = (ROOT / "config/supervisor/supervisord.conf").read_text()
    matrix = assess_monitoring_capabilities(current)
    assert matrix == {
        "qualified_entry_to_live_admission": "MISSING_OR_DISCONNECTED",
        "lifecycle_polling_persistence_terminal_history": "MISSING_OR_DISCONNECTED",
        "helius_monitor_replacement_equivalence": "UNPROVEN",
    }
    assert deployment_safe(matrix) is False
