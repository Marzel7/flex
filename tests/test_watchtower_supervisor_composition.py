import hashlib

from src.ops.watchtower_supervisor_composition import (
    RuntimePaths,
    compose,
    compose_api_only,
    listener_bytes,
)


SOURCE = b'''[unix_http_server]\nfile=/tmp/live.sock\n\n[supervisord]\npidfile=/tmp/live.pid\n\n[program:watchtower_api]\ncommand=old-api\ndirectory=/old\nenvironment=LEGACY="1"\nautostart=true\n\n[program:watchtower_listener]\ncommand=listener\ndirectory=/listener\nenvironment=KEEP="yes"\nautostart=true\nautorestart=true\nstdout_logfile=/logs/listener.log\n\n[program:unrelated]\ncommand=keep-me\nautostart=true\n\n[program:operation_monitor_worker]\ncommand=old-worker\ndirectory=/old\nautostart=false\n\n'''


def _paths(tmp_path):
    return RuntimePaths(
        root="/candidate", sha="1bfacfc8683b03c4c0ac1cf5276d3bedf47990b1", python="/python",
        canonical_db="/canonical.db", api_operations_db="/api-ops.db",
        worker_operations_db="/worker-ops.db", api_queue="/api-queue",
        worker_queue="/worker-queue", api_monitor_ui_db="/ui-ops.db",
        monitor_state_root="/state", monitor_env_file="/env", audit_ledger="/audit.json",
        shadow_ledger=str(tmp_path / "shadow.json"), api_stdout_log="/logs/api.log",
        api_stderr_log="/logs/api.err", worker_stdout_log="/logs/worker.log",
        worker_stderr_log="/logs/worker.err",
    )


def test_composition_preserves_listener_and_unrelated_bytes(tmp_path):
    candidate = compose(SOURCE, _paths(tmp_path))
    assert listener_bytes(candidate) == listener_bytes(SOURCE)
    assert b"[program:unrelated]\ncommand=keep-me\nautostart=true\n\n" in candidate
    assert candidate.count(b"[program:watchtower_listener]") == 1
    assert candidate.count(b"[program:watchtower_api]") == 1
    assert candidate.count(b"[program:operation_monitor_worker]") == 1
    assert b"directory=/candidate" in candidate
    assert b'WATCHTOWER_FINAL_SHA="1bfacfc8683b03c4c0ac1cf5276d3bedf47990b1"' in candidate
    assert b'WATCHTOWER_SHADOW_CAPTURE_ENABLED="0"' in candidate
    assert b"autostart=false\nautorestart=false\nstartretries=0" in candidate


def test_composition_is_idempotent_and_source_is_exact_rollback_artifact(tmp_path):
    paths = _paths(tmp_path)
    candidate = compose(SOURCE, paths)
    assert compose(candidate, paths) == candidate
    assert hashlib.sha256(SOURCE).hexdigest() == hashlib.sha256(bytes(SOURCE)).hexdigest()


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
        b"[program:operation_monitor_worker]\ncommand=old-worker\ndirectory=/old\nautostart=false\n\n",
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
