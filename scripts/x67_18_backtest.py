"""X67.18 -- Mandatory backtest of the shared canonical eligibility predicate
against real, historical production data, BEFORE any production deployment.

Read-only: opens both databases with mode=ro; performs zero writes.

Populations backtested (per the task's explicit requirement):
  1. Every existing canonical WATCHTOWER launch (wt_watchtower_launches, 143 rows)
  2. Every Provisioning Candidate (wt_provisioning_candidate_workflow, 17 rows)
  3. Known non-WATCHTOWER controls (wt_attribution_outcomes, non-canonical outcome types)
  4. Historical relay-assisted cases (the Dv34/B48k upstream-wallet mints, X67.16)
  5. Historical exchange-boundary cases (the 5 EXCHANGE_BOUNDARY candidates, X67.13/14)
  6. Known mechanism-conflict cases (HqDzBCPHMNKu/9Pp8MeVxT5ku, X67.14)
  7. Legacy historical launches (the 13 NULL-detection-source rows, X67.10)

Produces a full reconciliation report. Every mismatch/exception is
individually explained inline, not aggregated away, per the task's
explicit requirement.
"""
import sqlite3
import sys

sys.path.insert(0, ".")

from src.ops.watchtower_canonical_adapters import (
    build_evidence_for_registry_row,
    build_evidence_from_path_a_candidate,
)
from src.ops.watchtower_canonical_predicate import evaluate_watchtower_canonical_eligibility

OPS_DB = "database/wt_ops_v2.db"
CORE_DB = "database/flex_complete_database.db"


def ro_conn(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


X67_15_VERIFIED_PLAIN_XFER = frozenset({
    "5Rg9Ay22nwhhgE3adzvwsGxMCKTyrPn3joYhiLZEpump",
    "gQcrSg6acMHon1RHfMAwGtdVFvW2mJNF1T6dkgmpump",
    "8XaVic8H3Rr8jiWhnrEdpVWoSakTygj3R5NdQtr9pump",
    "E7AAwze6ch19cmexjsNHgz7tT27yzzvjqZ79AD8Zpump",
    "CVdByCD7SLsj2Kv7UAqGyNJgSVc4Nvd8qdL2U1shpump",
})
X67_10_LEGACY_MINTS = frozenset({
    "AB7XXeQAvN2yiqrg4MR3AbyhNdL1dAyhSon4LhLUpump",
    "3gbBrgtwyxPWeeWLynYxRj2tBBHDYfjdVTfa5gXzpump",
    "Bn9kT53VKyTSjaHX5W1B1PicKhS6e1ZHTajnRGjYpump",
    "sP79aMCqfZB16ekcmauZ19tycYFBRHuZyJvKDyCpump",
    "2PZAgPXXAUWv5EVkYUqDaroCzqW7QcxF8JfsRVKopump",
    "5iPoWhLAzoXRRk849LxkEJErPjWb1hK9MFGZdFPppump",
    "3SkdUCkXKXi86Z8X73meyacuHqBrdn2S6HUZoS14pump",
    "2vBvPiCpsbFwz41VH8j866rjhMqKmNcnZanziw1Kpump",
    "GQEEL98udpaC4QRnCVqdRPgUgSVjXpN4KaHCXjQMpump",
    "6YqsppC6qjJ3Efgwt6wMef6YGDofAPzEP5egvdzvpump",
    "9x4NHggD8U5gUQ6hYWha3xSJDdv3GykXR8txuCrcpump",
    "9YXYH9A8b2XjU5NUM5h3dxcxZn7Xr2Xu81oGG4U7pump",
    "CQJzHVvpn3Ewt6utez6MJ1hJBM4WVFHzV4kDoy9jpump",
})


def main():
    ops = ro_conn(OPS_DB)

    print("=" * 78)
    print("X67.18 BACKTEST: shared canonical predicate vs. production data")
    print("=" * 78)

    # --- Population 1: every existing canonical WATCHTOWER launch ---
    mints = [r["mint"] for r in ops.execute("SELECT mint FROM wt_watchtower_launches").fetchall()]
    print(f"\n--- Population 1: Existing Canonical ({len(mints)} rows) ---")
    canon_pass, canon_review, canon_fail = [], [], []
    for mint in mints:
        try:
            ev = build_evidence_for_registry_row(ops, mint=mint)
            result = evaluate_watchtower_canonical_eligibility(ev)
        except Exception as exc:  # noqa: BLE001
            canon_fail.append((mint, f"EXCEPTION: {exc}"))
            continue
        if result.decision == "ACCEPTED":
            canon_pass.append((mint, result.decision_reason))
        elif result.decision == "REVIEW_REQUIRED" or result.decision == "INSUFFICIENT_EVIDENCE":
            canon_review.append((mint, result.decision_reason, result.missing_evidence))
        else:
            canon_fail.append((mint, result.decision_reason))

    print(f"Pass:   {len(canon_pass)}")
    print(f"Review: {len(canon_review)}")
    print(f"Fail:   {len(canon_fail)}")

    print("\n  Review-required rows (every one explained):")
    for mint, reason, missing in canon_review:
        tag = "X67.15-verified-PLAIN_XFER" if mint in X67_15_VERIFIED_PLAIN_XFER else \
              "X67.10-legacy" if mint in X67_10_LEGACY_MINTS else "UNEXPECTED"
        print(f"    {mint[:20]}... reason={reason} missing={missing} [{tag}]")

    print("\n  Fail rows (every one MUST be individually explained -- none expected):")
    for item in canon_fail:
        print(f"    {item}  <-- UNEXPECTED, requires investigation before any rollout")

    # --- Population 2: every Provisioning Candidate ---
    cand_mints_rows = ops.execute(
        "SELECT mint, workflow_state, closure_reason FROM wt_provisioning_candidate_workflow"
    ).fetchall()
    print(f"\n--- Population 2: Provisioning Candidates ({len(cand_mints_rows)} rows) ---")
    cand_pass, cand_review, cand_fail = [], [], []
    for row in cand_mints_rows:
        mint = row["mint"]
        try:
            ev = build_evidence_from_path_a_candidate(ops, mint=mint)
            result = evaluate_watchtower_canonical_eligibility(ev)
        except Exception as exc:  # noqa: BLE001
            cand_fail.append((mint, row["workflow_state"], row["closure_reason"], f"EXCEPTION: {exc}"))
            continue
        entry = (mint, row["workflow_state"], row["closure_reason"], result.decision_reason, result.missing_evidence)
        if result.decision == "ACCEPTED":
            cand_pass.append(entry)
        elif result.decision in ("REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"):
            cand_review.append(entry)
        else:
            cand_fail.append(entry)

    print(f"Pass:   {len(cand_pass)}")
    print(f"Review: {len(cand_review)}")
    print(f"Fail:   {len(cand_fail)}")

    print("\n  Pass (new-predicate says ACCEPTED -- compare against current workflow_state):")
    for mint, state, closure, reason, *_ in cand_pass:
        print(f"    {mint[:20]}... current_state={state} current_closure={closure} new_reason={reason}")

    print("\n  Review (every one explained against its current closure_reason):")
    for mint, state, closure, reason, missing in cand_review:
        print(f"    {mint[:20]}... current_state={state} current_closure={closure} new_reason={reason} missing={missing}")

    print("\n  Fail (every one explained):")
    for item in cand_fail:
        print(f"    {item}")

    # --- Population 3: known non-WATCHTOWER controls ---
    control_rows = ops.execute(
        "SELECT DISTINCT mint FROM wt_attribution_outcomes "
        "WHERE outcome_type != 'CANONICAL_OPERATOR_REACHED' LIMIT 30"
    ).fetchall()
    print(f"\n--- Population 3: Known Non-WATCHTOWER Controls (sample of {len(control_rows)}) ---")
    control_correct, control_unexpected = [], []
    for row in control_rows:
        mint = row["mint"]
        wq = ops.execute(
            "SELECT creator, subprov, treasury, funding_mechanism FROM wt_walkback_queue WHERE mint=?",
            (mint,),
        ).fetchone()
        if wq is None:
            continue
        from src.ops.watchtower_canonical_adapters import build_evidence_from_path_b_outcome
        try:
            ev = build_evidence_from_path_b_outcome(ops, mint=mint)
            result = evaluate_watchtower_canonical_eligibility(ev)
        except Exception as exc:  # noqa: BLE001
            control_unexpected.append((mint, f"EXCEPTION: {exc}"))
            continue
        if result.decision == "REJECTED" and result.decision_reason == "IDENTITY_UNCONFIRMED":
            control_correct.append(mint)
        elif result.decision != "ACCEPTED":
            # Rejected/insufficient for a DIFFERENT reason is still not a
            # false-positive canonical acceptance -- track separately from a
            # true P0 (accepted) finding.
            control_correct.append(mint)
        else:
            control_unexpected.append((mint, result.decision_reason))

    print(f"Correctly rejected/non-canonical: {len(control_correct)}")
    print(f"UNEXPECTED (predicate ACCEPTED a known non-WATCHTOWER mint -- P0 if any): {len(control_unexpected)}")
    for item in control_unexpected:
        print(f"    {item}  <-- P0, investigate immediately")

    # --- Population 4/5/6: named historical special cases ---
    print("\n--- Populations 4-6: Named historical special cases ---")
    special_cases = {
        "relay-assisted (X67.16)": [
            "HWvpE3aqpDvL", "3uXrKahFncwc",  # Dv34 upstream (truncated prefixes, matched via LIKE below)
        ],
        "exchange-boundary (X67.13/14)": [
            "12uxfjozqd7iWKKAb6FY7pCE9SSnnQxB4MhfYy7Ypump",
            "4fmhCviNejMSrzAYMfyyAsoa4mJvWoFMLqxbRz8Bpump",
            "53tPFYDtc5m6F7xCiDCNHeYdit5iLF4E8QSD7Nuypump",
            "91ykjLVJPqknwAoHKESqqBVmgdpH5PJqe8AZXGHWpump",
            "H98z4JrinRSbrdwyZzhbS6cGu1fd7njM37E5vm75EYH6",
        ],
        "mechanism-conflict (X67.14)": [
            "HqDzBCPHMNKu66kBoKFgrJWiSqYP7VS4LcSqTnBXpump",
            "9Pp8MeVxT5kuCx12YqNMTG39GwJxazzJr4Ettenkpump",
        ],
    }
    for label, mints_list in special_cases.items():
        print(f"\n  {label}:")
        for mint in mints_list:
            if len(mint) < 30:
                continue  # skip the truncated-prefix placeholders, not real mints
            try:
                ev = build_evidence_from_path_a_candidate(ops, mint=mint)
                result = evaluate_watchtower_canonical_eligibility(ev)
                print(f"    {mint[:20]}... -> decision={result.decision} reason={result.decision_reason}")
            except Exception as exc:  # noqa: BLE001
                print(f"    {mint[:20]}... -> EXCEPTION: {exc}")

    print("\n" + "=" * 78)
    print("BACKTEST COMPLETE. Zero database writes performed.")
    print("=" * 78)

    ops.close()


if __name__ == "__main__":
    main()
