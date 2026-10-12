"""Bounded DEV028 Watchtower 30-second post-peak evidence study.

The module deliberately reuses the existing Birdeye binding, DevProviderBudget,
and sharp-exit normalizer.  It owns only a compact study manifest/journal; it
never retains provider payloads or full candle histories.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from src.ops.dev_provider_budget import BudgetDenied, DevProviderBudget
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request
from src.ops.watchtower_sharp_exit_evidence import (
    CONTRACT_VERSION, WINDOW_AFTER_SECONDS, WINDOW_BEFORE_SECONDS,
    ExitEvidenceValidationError, compact_failure_diagnostic,
    normalize_30s_exit_evidence, request_identity,
)

MANIFEST_SCHEMA = "DEV028_WATCHTOWER_SHARP_EXIT_MANIFEST_V1"
JOURNAL_SCHEMA = "DEV028_WATCHTOWER_SHARP_EXIT_JOURNAL_V1"
MAX_REQUESTS = 10
PACE_SECONDS = 6
MAX_JOURNAL_BYTES = 1_000_000
KNOWN_EXAMPLES = {
    "6LdjC13zbAxmruKrqsAQ4znUrjNe7gXXVeCHDBMYpump",
    "B5yWXR3PZeRuEDs7P55SBgyZEiVu3Ak7k3uWmYLdpump",
    "2GMowfFBuhy6ZxHMdKiC8fTKZFQhnCnmC5x27bTSpump",
}
TERMINAL = {"QUALIFIED_CRASH_OBSERVED", "NO_85_PERCENT_CANDLE_OBSERVED", "INSUFFICIENT_EVIDENCE", "OBSERVED_PARTIAL", "OUTCOME_UNKNOWN"}
# A retained monitor bucket does not establish an opening reference.  Its scalar
# value may be a close-only observation and, unlike the qualified 1s/1m opening
# baselines, carries no opening-field semantic.
INCOMPATIBLE_OPENING_BASELINE_TYPES = frozenset({"FIRST_RETAINED_MONITOR_CANDLE"})


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (_canonical(value) + "\n").encode()
    if len(encoded) > MAX_JOURNAL_BYTES:
        raise RuntimeError("DEV028_COMPACT_STORAGE_BOUND_EXCEEDED")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text())
    if not isinstance(loaded, dict):
        raise ValueError("DEV028_INVALID_JSON_OBJECT")
    return loaded


def _load_lifecycle(lifecycle_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(lifecycle_root.glob("journals/*/journal.json")):
        row = _read_json(path)
        lifecycle = row.get("lifecycle_metrics")
        if row.get("outcome") == "COMPLETED" and isinstance(lifecycle, dict) and isinstance(row.get("mint"), str):
            result[row["mint"]] = row
    return result


def _selection_rows(prepeak_db: Path, lifecycle_root: Path) -> list[dict[str, Any]]:
    lifecycle = _load_lifecycle(lifecycle_root)
    connection = sqlite3.connect(f"file:{prepeak_db}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT mint, selection_json, state FROM dev020_prepeak_tokens").fetchall()
    finally:
        connection.close()
    candidates: list[dict[str, Any]] = []
    for mint, selection_json, prepeak_state in rows:
        selection = json.loads(selection_json)
        baseline_type = selection.get("baseline_type")
        if baseline_type in INCOMPATIBLE_OPENING_BASELINE_TYPES:
            continue
        retained = lifecycle.get(mint)
        metrics = retained.get("lifecycle_metrics") if retained else None
        trough = metrics.get("peak_to_subsequent_trough") if isinstance(metrics, dict) else None
        values = (selection.get("opening_market_cap"), selection.get("opening_timestamp"), selection.get("observed_peak_market_cap"), selection.get("observed_peak_timestamp"), metrics.get("last_observed_timestamp") if isinstance(metrics, dict) else None)
        if mint in KNOWN_EXAMPLES or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) and value > 0 for value in values):
            continue
        opening_mc, opening_timestamp, peak_mc, peak_timestamp, lifecycle_end = values
        drawdown = trough.get("drawdown_percent") if isinstance(trough, dict) else None
        if lifecycle_end <= peak_timestamp or not isinstance(drawdown, (int, float)) or not math.isfinite(drawdown) or drawdown < 85:
            continue
        # The established normalizer's exact 4,800-second span requires a
        # 900-second aligned candidate.  Keep the study peak-aligned and never
        # expand it to chase an event later in lifecycle history.
        candidate_start = int(peak_timestamp) - (int(peak_timestamp) % 900)
        if lifecycle_end < candidate_start + 900:
            continue
        candidates.append({
            "mint": mint,
            "opening_mc_usd": float(opening_mc),
            "opening_timestamp": int(opening_timestamp),
            "opening_baseline_type": baseline_type,
            "observed_peak_mc_usd": float(peak_mc),
            "observed_peak_timestamp": int(peak_timestamp),
            "retained_lifecycle_end_timestamp": int(lifecycle_end),
            "retained_post_peak_duration_seconds": int(lifecycle_end - peak_timestamp),
            "retained_peak_to_subsequent_trough_drawdown_percent": float(drawdown),
            "prepeak_state": str(prepeak_state),
            "lifecycle_rank": retained.get("rank"),
            "lifecycle_coverage": metrics.get("coverage_confidence"),
            "candidate_start": candidate_start,
        })
    return sorted(candidates, key=lambda item: (item["observed_peak_mc_usd"] / item["opening_mc_usd"], item["retained_post_peak_duration_seconds"], item["mint"]))


def prepare_manifest(*, prepeak_db: Path, lifecycle_root: Path, destination: Path) -> dict[str, Any]:
    candidates = _selection_rows(prepeak_db, lifecycle_root)
    if len(candidates) < MAX_REQUESTS:
        raise RuntimeError("DEV028_INSUFFICIENT_QUALIFIED_CANDIDATES")
    positions = [round(index * (len(candidates) - 1) / (MAX_REQUESTS - 1)) for index in range(MAX_REQUESTS)]
    selected = [candidates[position] for position in positions]
    records = []
    for ordinal, record in enumerate(selected, start=1):
        candidate_start = record["candidate_start"]
        request_id = request_identity(mint=record["mint"], entry_timestamp=record["opening_timestamp"], candidate_start=candidate_start)
        records.append({**record, "ordinal": ordinal, "request_identity": request_id,
                        "request_window": {"time_from": candidate_start - WINDOW_BEFORE_SECONDS, "time_to": candidate_start + WINDOW_AFTER_SECONDS,
                                           "type": "30s", "chart_type": "mcap", "currency": "usd", "mode": "range", "padding": False}})
    content = {"schema": MANIFEST_SCHEMA, "maximum_requests": MAX_REQUESTS, "selection_method": "peak-multiple-stratified deterministic deciles; excludes previously known DEV026 examples; retained severe post-peak drawdown >=85%; fixed peak-aligned 4,800-second normalizer window", "records": records}
    manifest = {**content, "content_hash": _sha(content)}
    _atomic_json(destination, manifest)
    return manifest


def _verified_manifest(path: Path) -> dict[str, Any]:
    manifest = _read_json(path)
    supplied = manifest.pop("content_hash", None)
    maximum = manifest.get("maximum_requests")
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(maximum, int) or not 1 <= maximum <= MAX_REQUESTS or not isinstance(supplied, str) or supplied != _sha(manifest):
        raise ValueError("DEV028_MANIFEST_VERIFICATION_FAILED")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != maximum or len({record.get("request_identity") for record in records}) != maximum:
        raise ValueError("DEV028_MANIFEST_IDENTITY_FAILURE")
    manifest["content_hash"] = supplied
    return manifest


def prepare_corrected_continuation_manifest(*, original_manifest_path: Path, proof_journal_path: Path, destination: Path) -> dict[str, Any]:
    """Freeze the unproven remainder of the original cohort without substitution."""
    original = _verified_manifest(original_manifest_path)
    proof = _read_json(proof_journal_path)
    excluded_mint = proof.get("mint")
    if not isinstance(excluded_mint, str) or proof.get("provider_requests") != 1:
        raise ValueError("DEV028_PROOF_IDENTITY_UNVERIFIED")
    records = [record for record in original["records"] if record.get("mint") != excluded_mint]
    if len(records) != len(original["records"]) - 1 or not records:
        raise ValueError("DEV028_CONTINUATION_SELECTION_FAILURE")
    content = {
        "schema": MANIFEST_SCHEMA,
        "maximum_requests": len(records),
        "continuation_of_content_hash": original["content_hash"],
        "excluded_corrected_proof_mint": excluded_mint,
        "selection_method": "Exact original frozen DEV028 records excluding the separately completed corrected one-token proof; no substitutions.",
        "records": records,
    }
    continuation = {**content, "content_hash": _sha(content)}
    _atomic_json(destination, continuation)
    return continuation


def initialize_journal(*, manifest: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    if destination.exists():
        existing = _read_json(destination)
        if existing.get("manifest_content_hash") != manifest.get("content_hash"):
            raise ValueError("DEV028_JOURNAL_MANIFEST_MISMATCH")
        return existing
    journal = {"schema": JOURNAL_SCHEMA, "manifest_content_hash": manifest["content_hash"], "records": {record["request_identity"]: {"mint": record["mint"], "ordinal": record["ordinal"], "state": "PENDING", "provider_requests": 0} for record in manifest["records"]}}
    _atomic_json(destination, journal)
    return journal


def _read_authoritative_birdeye(env_path: Path) -> str:
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line.startswith("BIRDEYE="):
            continue
        value = line.split("=", 1)[1].strip().strip("'\"")
        if value:
            return value
        break
    raise RuntimeError("MISSING_AUTHORITATIVE_BIRDEYE_CREDENTIAL")


def _classify(normalized: Mapping[str, Any]) -> str:
    first = normalized.get("first_qualifying_collapse")
    if isinstance(first, Mapping):
        return "QUALIFIED_CRASH_OBSERVED" if normalized.get("coverage_classification") == "COMPLETE_30S_COVERAGE" else "OBSERVED_PARTIAL"
    if normalized.get("coverage_classification") == "PARTIAL_30S_COVERAGE":
        return "OBSERVED_PARTIAL"
    return "NO_85_PERCENT_CANDLE_OBSERVED"


def execute(*, manifest_path: Path, journal_path: Path, budget_root: Path, env_path: Path, binding_factory: Callable[[], Callable[[Mapping[str, Any]], Any]] | None = None, now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    manifest = _verified_manifest(manifest_path)
    journal = initialize_journal(manifest=manifest, destination=journal_path)
    # An interrupted post-admission attempt is never replayed automatically.
    for record in journal["records"].values():
        if record.get("state") in {"ADMISSION_INTENT", "ADMITTED", "ATTEMPTED"}:
            record["state"] = "OUTCOME_UNKNOWN"
            record["reason"] = "DURABLE_POST_ADMISSION_STATE_RECOVERY"
    _atomic_json(journal_path, journal)
    try:
        parsed_budget = _read_json(budget_root / "provider_budget.json")
        if not isinstance(parsed_budget.get("calls", []), list):
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        raise RuntimeError("DEV028_PROVIDER_BUDGET_LEDGER_UNREADABLE")
    credential = _read_authoritative_birdeye(env_path)
    os.environ["BIRDEYE"] = credential
    binding = binding_factory() if binding_factory else BirdeyeProductionBinding(credential_label="BIRDEYE")
    transport_count = sum(int(item.get("provider_requests", 0)) for item in journal["records"].values())
    if transport_count > MAX_REQUESTS:
        raise RuntimeError("DEV028_REQUEST_CAP_ALREADY_EXCEEDED")
    last_transport_at: float | None = None
    for item in manifest["records"]:
        state = journal["records"][item["request_identity"]]
        if state.get("state") in TERMINAL:
            continue
        if state.get("state") != "PENDING" or transport_count >= MAX_REQUESTS:
            raise RuntimeError("DEV028_NON_RETRYABLE_STATE_OR_CAP")
        if last_transport_at is not None:
            remaining = PACE_SECONDS - (now() - last_transport_at)
            if remaining > 0:
                sleep(remaining)
        state["state"] = "ADMISSION_INTENT"
        _atomic_json(journal_path, journal)
        try:
            # DEV028 is intentionally stricter than the shared ledger's
            # default global ceiling: this bounded study permits at most ten
            # admissions in its rolling minute.
            admission = DevProviderBudget(budget_root, now=now).admit(item["mint"], "DEV028_30S_SHARP_EXIT", global_limit=10)
        except BudgetDenied as error:
            state.update({"state": "PENDING", "reason": str(error)})
            _atomic_json(journal_path, journal)
            break
        state.update({"state": "ADMITTED", "budget_admission": admission})
        _atomic_json(journal_path, journal)
        state.update({"state": "ATTEMPTED", "provider_requests": int(state.get("provider_requests", 0)) + 1})
        _atomic_json(journal_path, journal)
        transport_count += 1
        request = build_birdeye_ohlcv_request(address=item["mint"], interval="30s", time_from=item["request_window"]["time_from"], time_to=item["request_window"]["time_to"])
        last_transport_at = now()
        outcome = binding(request)
        if outcome.status_code != 200 or outcome.error_state is not None:
            state.update({"state": "INSUFFICIENT_EVIDENCE", "http_status": outcome.status_code, "transport_error": outcome.error_state})
        else:
            try:
                normalized = normalize_30s_exit_evidence(outcome=outcome, mint=item["mint"], entry_timestamp=item["opening_timestamp"], entry_mc_usd=item["opening_mc_usd"], candidate_start=item["candidate_start"], request_id=item["request_identity"], provider_provenance="BIRDEYE_V3_30S_MCAP", observed_peak_timestamp=item["observed_peak_timestamp"])
                state.update({"state": _classify(normalized), "http_status": outcome.status_code, "evidence": normalized})
            except ExitEvidenceValidationError as error:
                # Keep only the established bounded, field-presence diagnostic;
                # never retain provider values or full candle history.
                state.update({"state": "INSUFFICIENT_EVIDENCE", "http_status": outcome.status_code,
                              "normalization_diagnostic": compact_failure_diagnostic(error, request_id=item["request_identity"])})
            except ValueError as error:
                state.update({"state": "INSUFFICIENT_EVIDENCE", "http_status": outcome.status_code, "normalization_error": str(error)})
        _atomic_json(journal_path, journal)
    return _read_json(journal_path)


def execute_single_corrected_proof(*, manifest_path: Path, original_journal_path: Path, proof_journal_path: Path,
                                  budget_root: Path, env_path: Path, ordinal: int = 1,
                                  binding_factory: Callable[[], Callable[[Mapping[str, Any]], Any]] | None = None,
                                  now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Run one explicitly authorized corrected-normalizer proof without mutating DEV028 outcomes."""
    if proof_journal_path.exists():
        raise RuntimeError("DEV028_PROOF_JOURNAL_ALREADY_EXISTS")
    manifest = _verified_manifest(manifest_path)
    original = _read_json(original_journal_path)
    selected = next((item for item in manifest["records"] if item["ordinal"] == ordinal), None)
    if selected is None:
        raise ValueError("DEV028_PROOF_ORDINAL_NOT_FROZEN")
    prior = original.get("records", {}).get(selected["request_identity"])
    if not isinstance(prior, Mapping) or prior.get("state") != "INSUFFICIENT_EVIDENCE":
        raise RuntimeError("DEV028_ORIGINAL_OUTCOME_NOT_PRESERVED")
    try:
        ledger = _read_json(budget_root / "provider_budget.json")
        if not isinstance(ledger.get("calls", []), list):
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        raise RuntimeError("DEV028_PROVIDER_BUDGET_LEDGER_UNREADABLE")
    proof_identity = _sha({"schema": "DEV028_CORRECTED_30S_PROOF_V1", "original_request_identity": selected["request_identity"]})
    journal: dict[str, Any] = {"schema": "DEV028_CORRECTED_30S_PROOF_V1", "proof_identity": proof_identity,
                                "original_request_identity": selected["request_identity"], "mint": selected["mint"],
                                "state": "PENDING", "provider_requests": 0}
    _atomic_json(proof_journal_path, journal)
    credential = _read_authoritative_birdeye(env_path)
    os.environ["BIRDEYE"] = credential
    binding = binding_factory() if binding_factory else BirdeyeProductionBinding(credential_label="BIRDEYE")
    journal["state"] = "ADMISSION_INTENT"; _atomic_json(proof_journal_path, journal)
    try:
        journal["budget_admission"] = DevProviderBudget(budget_root, now=now).admit(selected["mint"], "DEV028_CORRECTED_30S_PROOF", global_limit=10)
    except BudgetDenied as error:
        journal.update({"state": "PENDING", "reason": str(error)}); _atomic_json(proof_journal_path, journal); return journal
    journal.update({"state": "ADMITTED"}); _atomic_json(proof_journal_path, journal)
    journal.update({"state": "ATTEMPTED", "provider_requests": 1}); _atomic_json(proof_journal_path, journal)
    request = build_birdeye_ohlcv_request(address=selected["mint"], interval="30s", time_from=selected["request_window"]["time_from"], time_to=selected["request_window"]["time_to"])
    outcome = binding(request)
    if outcome.status_code != 200 or outcome.error_state is not None:
        journal.update({"state": "INSUFFICIENT_EVIDENCE", "http_status": outcome.status_code, "transport_error": outcome.error_state})
    else:
        try:
            normalized = normalize_30s_exit_evidence(outcome=outcome, mint=selected["mint"], entry_timestamp=selected["opening_timestamp"], entry_mc_usd=selected["opening_mc_usd"], candidate_start=selected["candidate_start"], request_id=selected["request_identity"], provider_provenance="BIRDEYE_V3_30S_MCAP", observed_peak_timestamp=selected["observed_peak_timestamp"])
            journal.update({"state": _classify(normalized), "http_status": outcome.status_code, "evidence": normalized})
        except ExitEvidenceValidationError as error:
            journal.update({"state": "INSUFFICIENT_EVIDENCE", "http_status": outcome.status_code,
                            "normalization_diagnostic": compact_failure_diagnostic(error, request_id=selected["request_identity"])})
        except ValueError as error:
            journal.update({"state": "INSUFFICIENT_EVIDENCE", "http_status": outcome.status_code, "normalization_error": str(error)})
    _atomic_json(proof_journal_path, journal)
    return journal
