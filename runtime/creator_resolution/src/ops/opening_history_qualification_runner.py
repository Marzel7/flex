"""Offline execution of the frozen three-row adapter qualification corpus."""
from __future__ import annotations
import hashlib, json, shutil, tempfile
from pathlib import Path

from .opening_history_batch_executor import DurableOpeningHistoryExecutor
from .opening_history_execution_adapter import OpeningHistoryExecutionAdapter
from .opening_history_fixture_transport import ExactQualificationFixtureTransport, VERSION as TRANSPORT_VERSION

ROOT = Path(__file__).resolve().parents[2]
QUALIFICATION = ROOT / "tests/fixtures/opening_history_batch_executor/opening_history_3row_qualification_manifest.v1.json"
FIXTURES = ROOT / "tests/fixtures/opening_history_batch_executor/derived_3row_v1/manifest.v1.json"
PRODUCTION = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_399_opening_history_execution_manifest.v1.json"
PRODUCTION_SHA = "2d60d4052a6726d4adf90ff9989b39def40a0093f99fcc86476bd39ab23b81b5"

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def run() -> dict:
    assert sha(FIXTURES) == "98a13bc21d32834f84c3034c345f86b5e0108c6bbb71d4fbe449aec9ae049b09"
    production = json.loads(PRODUCTION.read_text())
    assert sha(PRODUCTION) == PRODUCTION_SHA and len(production["rows"]) == 399 and [len(x["mints"]) for x in production["batches"]] == [50] * 7 + [49]
    transport = ExactQualificationFixtureTransport(QUALIFICATION, FIXTURES)
    executor = DurableOpeningHistoryExecutor(QUALIFICATION, sha(QUALIFICATION), Path(tempfile.mkdtemp(prefix="opening-history-qualification-")) / "state.json")
    root = executor.s.parent
    try:
        state = executor.runtime(root, hard_durable_ceiling=1048576)
        fixture_rows = {row["mint"]: row for row in json.loads(FIXTURES.read_text())["rows"]}
        # The row-1 event identities are the explicitly frozen template; row 2 intentionally changes B2.
        profile = {"fingerprint": ["5JTvwLFy1EPWoSWbVqjp4x9rfg961P9iKV3vsSubDFXd", "J4iit8MmXd2P46DzQxk7dj3qjY3m9G3Ck5zrPkBV54JV", "7j4uWuDdZnJFKMwQK4TezBhipVCugdUW3NzCW7CGecvC"]}
        adapter = OpeningHistoryExecutionAdapter(transport, profile)
        results = {}
        for row in executor.verify()["rows"]:
            record = adapter.execute_row(executor, state, row)
            stored = Path(state["durable_dir"]) / (row["mint"] + ".json")
            assert stored.read_bytes() == json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            results[row["mint"]] = {"row_id": fixture_rows[row["mint"]]["row_id"], "terminal_status": record["terminal_status"], "fingerprint_status": record["fingerprint_status"], "record_digest": record["record_digest"], "record_bytes": len(stored.read_bytes()), "opening_events": len(record["B1_B2_B3"])}
        gate = executor.batch_gate(state, 1)
        assert gate["decision"] == "CONTINUE"
        assert not list(Path(state["scratch_dir"]).iterdir()) and state["raw_retention"] == 0
        assert len(state["ledger"]) == len(transport.calls) == 7
        assert all(entry["dispatch_intent_persisted"] and entry["response_consumed"] and entry["wire_bytes"] > 0 and entry["response_digest"] for entry in state["ledger"])
        ledger_summary = {}
        for mint, row in fixture_rows.items():
            entries = [entry for entry in state["ledger"] if entry["mint"] == mint]
            calls = [call for call in transport.calls if call["row_id"] == row["row_id"]]
            assert len(entries) == len(calls)
            assert all(entry["provider_method"] == call["provider_method"] and entry["wire_bytes"] == call["exact_response_bytes"] and entry["response_digest"] == call["response_sha256"] for entry, call in zip(entries, calls))
            ledger_summary[row["row_id"]] = {"logical_acquisitions": len(entries), "dispatch_intents": len(entries), "responses_consumed": len(entries), "accepted_before_terminal": sum(entry["accepted"] for entry in entries), "terminal_commit": state["rows"][mint] == "TERMINAL", "wire_bytes": sum(entry["wire_bytes"] for entry in entries), "checkpoint_sequence": [entry["checkpoint_sequence"] for entry in entries]}
        artifact = {"schema_version": "BYZANTINE_3ROW_ADAPTER_RUNNER_QUALIFICATION_V1", "qualification_manifest": str(QUALIFICATION.relative_to(ROOT)), "qualification_manifest_sha256": sha(QUALIFICATION), "derived_fixture_manifest": str(FIXTURES.relative_to(ROOT)), "derived_fixture_manifest_sha256": sha(FIXTURES), "fixture_transport_version": TRANSPORT_VERSION, "request_to_fixture_map": transport.calls, "fixture_responses_consumed": len(transport.calls), "exact_cumulative_wire_bytes": sum(x["exact_response_bytes"] for x in transport.calls), "executor_wire_bytes": state["wire_bytes_received"], "per_row_action_ledger_summary": ledger_summary, "per_row_terminal_results": results, "extension_accounting": {"reservations": 40-state["extension_blocks_remaining"], "create_plus_1_calls": state["getBlock_extension_logical_calls"], "create_plus_2_calls": 0}, "batch_gate": gate, "raw_scratch": {"raw_retention": state["raw_retention"], "scratch_residual": len(list(Path(state["scratch_dir"]).iterdir()))}, "production_regression": {"manifest_sha256": sha(PRODUCTION), "rows": len(production["rows"]), "batch_sizes": [len(x["mints"]) for x in production["batches"]], "batch_1_rows_attempted": 0}, "real_provider_calls": 0, "production_batch_1_rows": 0, "verdict": "BYZANTINE_3ROW_ADAPTER_RUNNER_QUALIFIED"}
        artifact["artifact_sha256"] = hashlib.sha256(json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return artifact
    finally:
        shutil.rmtree(root)


def write_artifact(path: Path) -> dict:
    """Persist only the compact qualification summary, never provider payloads."""
    artifact = run()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return artifact
