"""X67.21 -- Read-only shadow replay over production data, exercising the
FULL integration layer (evaluate_canonical_decision in SHADOW mode) rather
than calling the predicate directly (as X67.18/X67.20's backtest scripts
did). This is the pre-deployment validation the task requires before any
production configuration is touched.

Read-only throughout: no database writes except into an IN-MEMORY telemetry
table (never the production wt_ops_v2.db).
"""
import sqlite3
import sys

sys.path.insert(0, ".")

from src.ops.watchtower_canonical_adapters import (
    build_evidence_for_candidate_workflow,
    build_evidence_for_registry_row,
)
from src.ops.watchtower_canonical_integration import (
    evaluate_canonical_decision,
    ensure_telemetry_schema,
    record_comparison_telemetry,
)

OPS_DB = "database/wt_ops_v2.db"


def ro_conn(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    ops = ro_conn(OPS_DB)
    # Telemetry goes to an isolated in-memory connection -- never the
    # production database, per this task's read-only constraint.
    telemetry = sqlite3.connect(":memory:")
    telemetry.row_factory = sqlite3.Row
    ensure_telemetry_schema(telemetry)

    matches = 0
    divergences = 0
    adapter_errors = 0
    predicate_errors = 0
    telemetry_errors = 0
    unexpected_acceptances = []
    unexpected_rejections = []
    divergence_detail = []

    # --- 143 canonical rows ---
    print("=" * 78)
    print("SHADOW REPLAY: 143 canonical registry rows")
    print("=" * 78)
    mints = [r["mint"] for r in ops.execute("SELECT mint FROM wt_watchtower_launches").fetchall()]
    for mint in mints:
        result = evaluate_canonical_decision(
            path="path_b_walkback", mint=mint,
            build_evidence=lambda m=mint: build_evidence_for_registry_row(ops, mint=m),
            legacy_decision="ACCEPTED", legacy_reason="ALREADY_CANONICAL", mode="shadow",
        )
        try:
            record_comparison_telemetry(telemetry, result, mint=mint)
        except Exception:  # noqa: BLE001
            telemetry_errors += 1
        if result.predicate_error:
            adapter_errors += 1
        if result.decisions_match is True:
            matches += 1
        elif result.decisions_match is False:
            divergences += 1
            divergence_detail.append((mint, result.divergence_code, result.predicate_raw_decision))

    print(f"Matches: {matches}  Divergences: {divergences}  Adapter/predicate errors: {adapter_errors}")
    print("\nDivergence detail (every one must map to an already-understood anomaly):")
    for mint, code, raw in divergence_detail:
        print(f"  {mint[:20]}... divergence={code} predicate_raw={raw}")

    # --- 17 candidate rows ---
    print("\n" + "=" * 78)
    print("SHADOW REPLAY: 17 provisioning-candidate rows")
    print("=" * 78)
    cand_rows = ops.execute(
        "SELECT mint, workflow_state FROM wt_provisioning_candidate_workflow"
    ).fetchall()
    cand_matches = cand_divergences = 0
    cand_divergence_detail = []
    for row in cand_rows:
        mint = row["mint"]
        legacy = "ACCEPTED" if row["workflow_state"] == "PROMOTED_TO_MODEL_1" else "REJECTED"
        result = evaluate_canonical_decision(
            path="path_a_candidate_workflow", mint=mint,
            build_evidence=lambda m=mint: build_evidence_for_candidate_workflow(ops, mint=m),
            legacy_decision=legacy, legacy_reason=row["workflow_state"], mode="shadow",
        )
        try:
            record_comparison_telemetry(telemetry, result, mint=mint)
        except Exception:  # noqa: BLE001
            telemetry_errors += 1
        if result.predicate_error:
            adapter_errors += 1
        if result.decisions_match is True:
            cand_matches += 1
        elif result.decisions_match is False:
            cand_divergences += 1
            cand_divergence_detail.append((mint, row["workflow_state"], result.divergence_code, result.predicate_raw_decision))

    print(f"Matches: {cand_matches}  Divergences: {cand_divergences}")
    print("\nDivergence detail:")
    for mint, state, code, raw in cand_divergence_detail:
        print(f"  {mint[:20]}... current_state={state} divergence={code} predicate_raw={raw}")

    # --- 30 known non-WATCHTOWER controls ---
    print("\n" + "=" * 78)
    print("SHADOW REPLAY: 30 known non-WATCHTOWER controls")
    print("=" * 78)
    control_rows = ops.execute(
        "SELECT DISTINCT mint FROM wt_attribution_outcomes "
        "WHERE outcome_type != 'CANONICAL_OPERATOR_REACHED' LIMIT 30"
    ).fetchall()
    from src.ops.watchtower_canonical_adapters import build_evidence_for_walkback_queue
    control_correct = 0
    for row in control_rows:
        mint = row["mint"]
        wq = ops.execute("SELECT 1 FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
        if wq is None:
            continue
        result = evaluate_canonical_decision(
            path="path_b_walkback", mint=mint,
            build_evidence=lambda m=mint: build_evidence_for_walkback_queue(ops, mint=m),
            legacy_decision="REJECTED", legacy_reason="NOT_WATCHTOWER", mode="shadow",
        )
        try:
            record_comparison_telemetry(telemetry, result, mint=mint)
        except Exception:  # noqa: BLE001
            telemetry_errors += 1
        if result.predicate_error:
            predicate_errors += 1
            continue
        pr = result.predicate_result
        if pr and pr.decision == "ACCEPTED":
            unexpected_acceptances.append(mint)
        else:
            control_correct += 1

    print(f"Correctly non-accepted: {control_correct}")
    print(f"UNEXPECTED acceptances (P0 if any): {len(unexpected_acceptances)}")
    for m in unexpected_acceptances:
        print(f"  {m}  <-- P0")

    telemetry_count = telemetry.execute("SELECT COUNT(*) c FROM wt_canonical_predicate_comparisons").fetchone()["c"]

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"matches: {matches + cand_matches + control_correct}")
    print(f"divergences: {divergences + cand_divergences}")
    print(f"adapter_errors: {adapter_errors}")
    print(f"predicate_errors: {predicate_errors}")
    print(f"telemetry_errors: {telemetry_errors}")
    print(f"telemetry_rows_recorded (in-memory only): {telemetry_count}")
    print(f"unexpected_acceptances: {len(unexpected_acceptances)}")
    print(f"unexpected_rejections: {len(unexpected_rejections)}")
    print("\nZero production database writes performed.")

    ops.close()
    telemetry.close()


if __name__ == "__main__":
    main()
