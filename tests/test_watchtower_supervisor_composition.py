import hashlib
from dataclasses import replace

from src.ops.watchtower_supervisor_composition import (
    RuntimePaths,
    compose,
    compose_api_only,
    compose_api_and_worker,
    compose_monitor_only,
    compose_monitoring_topology,
    listener_bytes,
)


SOURCE = b'''[unix_http_server]\nfile=/tmp/live.sock\n\n[supervisord]\npidfile=/tmp/live.pid\n\n[program:watchtower_api]\ncommand=old-api\ndirectory=/old\nenvironment=LEGACY="1"\nautostart=true\n\n[program:watchtower_listener]\ncommand=listener\ndirectory=/listener\nenvironment=KEEP="yes"\nautostart=true\nautorestart=true\nstdout_logfile=/logs/listener.log\n\n[program:unrelated]\ncommand=keep-me\nautostart=true\n\n[program:operation_monitor_worker]\ncommand=old-worker\ndirectory=/old\nautostart=false\n\n[program:operation_monitor_bridge]\ncommand=old-bridge\ndirectory=/old\nautostart=false\n\n'''


def _paths(tmp_path):
    return RuntimePaths(
        root="/candidate", sha="1bfacfc8683b03c4c0ac1cf5276d3bedf47990b1", python="/python",
        canonical_db="/canonical.db", api_operations_db="/api-ops.db",
        worker_operations_db="/worker-ops.db", api_queue="/api-queue",
        worker_queue="/worker-queue", api_monitor_ui_db="/ui-ops.db",
        monitor_state_root="/state", monitor_env_file="/env", audit_ledger="/audit.json",
        shadow_ledger=str(tmp_path / "shadow.json"), api_stdout_log="/logs/api.log",
        api_stderr_log="/logs/api.err", worker_stdout_log="/logs/worker.log",
        worker_stderr_log="/logs/worker.err", canonical_birth_db="/canonical-birth.db",
        bridge_source_db="/source-ops.db", bridge_health_path="/state/bridge.json",
        bridge_stdout_log="/logs/bridge.log", bridge_stderr_log="/logs/bridge.err",
        monitor_selection="/qualified-selection.json",
    )


def test_composition_preserves_listener_and_unrelated_bytes(tmp_path):
    candidate = compose(SOURCE, _paths(tmp_path))
    assert listener_bytes(candidate) == listener_bytes(SOURCE)
    assert b"[program:unrelated]\ncommand=keep-me\nautostart=true\n\n" in candidate
    assert candidate.count(b"[program:watchtower_listener]") == 1
    assert candidate.count(b"[program:watchtower_api]") == 1
    assert candidate.count(b"[program:operation_monitor_worker]") == 1
    assert candidate.count(b"[program:operation_monitor_bridge]") == 1
    assert b"directory=/candidate" in candidate
    assert b"scripts/launch_watchtower_final.sh api" in candidate
    assert b'WATCHTOWER_FINAL_SHA="1bfacfc8683b03c4c0ac1cf5276d3bedf47990b1"' in candidate
    assert b'WATCHTOWER_SHADOW_CAPTURE_ENABLED="0"' in candidate
    assert b"autostart=false\nautorestart=false\nstartretries=0" in candidate


def test_composition_is_idempotent_and_source_is_exact_rollback_artifact(tmp_path):
    paths = _paths(tmp_path)
    candidate = compose(SOURCE, paths)
    assert compose(candidate, paths) == candidate
    assert hashlib.sha256(SOURCE).hexdigest() == hashlib.sha256(bytes(SOURCE)).hexdigest()


def test_composition_renders_opt_in_bounded_soak_controls(tmp_path):
    paths = replace(_paths(tmp_path), monitor_max_iterations="1",
                    monitor_provider_global_limit="0",
                    monitor_provider_token_limit="0", bridge_max_iterations="1")
    candidate = compose(SOURCE, paths)
    assert b'MONITOR_MAX_ITERATIONS="1"' in candidate
    assert b'MONITOR_PROVIDER_GLOBAL_LIMIT="0"' in candidate
    assert b'MONITOR_PROVIDER_TOKEN_LIMIT="0"' in candidate
    assert b'MONITOR_BRIDGE_SELECTION_PATH="/qualified-selection.json"' in candidate
    assert b'MONITOR_BRIDGE_MAX_ITERATIONS="1"' in candidate
    assert candidate.count(b"startsecs=0\nexitcodes=0\n") == 2
    assert b"[program:operation_monitor_worker]" in candidate
    assert b"[program:operation_monitor_bridge]" in candidate
    assert compose(candidate, paths) == candidate


def test_monitor_only_composition_preserves_api_listener_and_unrelated_bytes(tmp_path):
    paths = replace(_paths(tmp_path), monitor_max_iterations="1",
                    monitor_provider_global_limit="0",
                    monitor_provider_token_limit="0", bridge_max_iterations="1")
    candidate = compose_monitor_only(SOURCE, paths)
    assert listener_bytes(candidate) == listener_bytes(SOURCE)
    api_start = SOURCE.index(b"[program:watchtower_api]")
    api_end = SOURCE.index(b"[program:watchtower_listener]")
    candidate_api_start = candidate.index(b"[program:watchtower_api]")
    candidate_api_end = candidate.index(b"[program:watchtower_listener]")
    assert candidate[candidate_api_start:candidate_api_end] == SOURCE[api_start:api_end]
    assert b"[program:unrelated]\ncommand=keep-me\nautostart=true\n\n" in candidate
    assert candidate.count(b"startsecs=0\nexitcodes=0\n") == 2
    assert compose_monitor_only(candidate, paths) == candidate


def test_api_and_worker_composition_preserves_bridge_listener_and_unrelated_bytes(tmp_path):
    candidate = compose_api_and_worker(SOURCE, _paths(tmp_path))
    assert listener_bytes(candidate) == listener_bytes(SOURCE)
    for name in (b"[program:unrelated]", b"[program:operation_monitor_bridge]"):
        start = SOURCE.index(name)
        end = SOURCE.find(b"\n[program:", start + 1)
        if end < 0:
            end = len(SOURCE)
        candidate_start = candidate.index(name)
        candidate_end = candidate.find(b"\n[program:", candidate_start + 1)
        if candidate_end < 0:
            candidate_end = len(candidate)
        assert candidate[candidate_start:candidate_end] == SOURCE[start:end]
    assert b'OPERATION_MONITOR_CANONICAL_BIRTH_DB_PATH="/canonical-birth.db"' in candidate
    assert b'OPERATION_MONITOR_QUEUE_PATH="/api-queue"' in candidate
    assert b'WATCHTOWER_MONITOR_QUEUE_PATH="/worker-queue"' in candidate
    assert compose_api_and_worker(candidate, _paths(tmp_path)) == candidate


def test_continuous_monitor_programs_keep_startup_liveness_contract(tmp_path):
    candidate = compose(SOURCE, _paths(tmp_path))
    worker = candidate[candidate.index(b"[program:operation_monitor_worker]"):candidate.index(b"[program:operation_monitor_bridge]")]
    bridge = candidate[candidate.index(b"[program:operation_monitor_bridge]"):]
    assert b"startsecs=5\n" in worker
    assert b"startsecs=5\n" in bridge
    assert b"exitcodes=0" not in worker
    assert b"exitcodes=0" not in bridge


def test_composition_rejects_missing_duplicate_or_include_targets(tmp_path):
    paths = _paths(tmp_path)
    for broken, reason in ((SOURCE.replace(b"[program:watchtower_listener]", b"[program:other]"), "CARDINALITY"),
                           (SOURCE + b"[program:watchtower_api]\ncommand=dup\n", "CARDINALITY"),
                           (SOURCE + b"[include]\nfiles=*.conf\n", "INCLUDE")):
        try:
            compose(broken, paths)
        except ValueError as error:
            assert reason in str(error)
        else:
            raise AssertionError("expected fail closed")


def test_api_only_composition_preserves_every_non_api_byte_and_does_not_add_worker(tmp_path):
    source = SOURCE.replace(
        b"[program:operation_monitor_worker]\ncommand=old-worker\ndirectory=/old\nautostart=false\n\n"
        b"[program:operation_monitor_bridge]\ncommand=old-bridge\ndirectory=/old\nautostart=false\n\n",
        b"",
    )
    candidate = compose_api_only(source, _paths(tmp_path))
    assert candidate.count(b"[program:operation_monitor_worker]") == 0
    assert listener_bytes(candidate) == listener_bytes(source)
    assert compose_api_only(candidate, _paths(tmp_path)) == candidate

    from src.ops.watchtower_supervisor_composition import _program_ranges
    start, end = _program_ranges(source.decode())["watchtower_api"][0]
    candidate_start, candidate_end = _program_ranges(candidate.decode())["watchtower_api"][0]
    assert candidate[:start] == source[:start]
    assert candidate[candidate_end:] == source[end:]


def test_monitoring_topology_adds_only_disabled_worker_and_bridge(tmp_path):
    source = SOURCE.replace(
        b"[program:operation_monitor_worker]\ncommand=old-worker\ndirectory=/old\nautostart=false\n\n"
        b"[program:operation_monitor_bridge]\ncommand=old-bridge\ndirectory=/old\nautostart=false\n\n",
        b"",
    )
    candidate = compose_monitoring_topology(source, _paths(tmp_path))
    assert candidate.count(b"[program:watchtower_listener]") == 1
    assert candidate.count(b"[program:operation_monitor_worker]") == 1
    assert candidate.count(b"[program:operation_monitor_bridge]") == 1
    assert b"scripts/launch_watchtower_final.sh worker" in candidate
    assert b"src.ops.operation_monitor_bridge_service" in candidate
    assert b'OPERATIONS_MODE="MONITOR"' in candidate
    assert b'MONITOR_BRIDGE_QUEUE_PATH="/worker-queue"' in candidate
    assert candidate.count(b"autostart=false\nautorestart=false\nstartretries=0") >= 2
    assert compose_monitoring_topology(candidate, _paths(tmp_path)) == candidate


def test_monitoring_topology_rejects_partial_or_duplicate_existing_topology(tmp_path):
    source = SOURCE.replace(
        b"[program:operation_monitor_worker]\ncommand=old-worker\ndirectory=/old\nautostart=false\n\n"
        b"[program:operation_monitor_bridge]\ncommand=old-bridge\ndirectory=/old\nautostart=false\n\n",
        b"",
    )
    partial = source + b"[program:operation_monitor_worker]\ncommand=old\n"
    try:
        compose_monitoring_topology(partial, _paths(tmp_path))
    except ValueError as error:
        assert "ALREADY_PRESENT_OR_PARTIAL" in str(error)
    else:
        raise AssertionError("expected fail closed")
