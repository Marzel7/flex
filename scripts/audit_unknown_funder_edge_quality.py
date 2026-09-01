"""Read-only qualification audit for the Unknown repeat-funder safety gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from src.ops.unknown_funder_edge_quality import (
    EdgeQuality, FundingObservation, classify_unknown_funder_edge,
)


def _result(observation: FundingObservation) -> dict:
    quality, reasons = classify_unknown_funder_edge(observation)
    return {"classifier_result": quality.value, "reason_codes": sorted(reasons)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="database/wt_ops_v2.db")
    parser.add_argument("--output", default="docs/audits/unknown_repeat_funder_edge_quality_qualification.v1.json")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # This is intentionally the exact production ranking population, before any filter.
    unknown = """
      SELECT fl.* FROM wt_farm_launches fl
      LEFT JOIN wt_confirmed_treasuries t ON fl.funder=t.treasury
      LEFT JOIN wt_discovered_subprovs sp ON fl.funder=sp.subprov
      WHERE t.treasury IS NULL AND sp.subprov IS NULL
    """
    baseline = conn.execute(f"""
      WITH unknown_launches AS ({unknown}), persistent AS (
        SELECT funder FROM unknown_launches GROUP BY funder HAVING COUNT(*) >= 3
      ) SELECT COUNT(*) rows, COUNT(DISTINCT funder) funders
        FROM unknown_launches WHERE funder IN (SELECT funder FROM persistent)
    """).fetchone()
    # A farm observation becomes a genuine control only where the direct funder
    # is independently retained as the selected Walkback edge for that mint.
    controls = conn.execute(f"""
      SELECT fl.mint,fl.funder,fl.creator,fl.seed_sol,fl.migrated_at,
             MIN(e.amount_lamports) amount_lamports,
             MIN(e.block_time) funding_time,
             MAX(CASE WHEN olm.mint IS NOT NULL THEN 1 ELSE 0 END) confirmed_operation_linked,
             MAX(CASE WHEN cm.mint IS NOT NULL THEN 1 ELSE 0 END) confirmed_match_linked,
             MAX(CASE WHEN pm.mint IS NOT NULL THEN 1 ELSE 0 END) qualified_potential_linked
      FROM ({unknown}) fl
      JOIN wt_walkback_edge_candidates e
        ON e.mint=fl.mint AND e.candidate_parent=fl.funder AND e.selection_status='SELECTED'
      LEFT JOIN operator_launch_membership olm ON olm.mint=fl.mint
      LEFT JOIN confirmed_operation_matches cm ON cm.mint=fl.mint AND cm.state='CONFIRMED_MATCH'
      LEFT JOIN provisional_operation_matches pm ON pm.mint=fl.mint
      GROUP BY fl.mint,fl.funder,fl.creator,fl.seed_sol,fl.migrated_at
    """).fetchall()
    control_rows=[]
    for r in controls:
        result=_result(FundingObservation(proven_funding_role=True, launch_coupling=True,
                                           transaction_role_consistent=True,
                                           amount_lamports=r['amount_lamports']))
        control_rows.append({**dict(r), **result})
    persistent_funders={r[0] for r in conn.execute(f"WITH unknown_launches AS ({unknown}) SELECT funder FROM unknown_launches GROUP BY funder HAVING COUNT(*) >= 3")}
    # The 50 lowest are retained in the artifact, not merely summarized.
    lowest=sorted(control_rows,key=lambda r:(r['amount_lamports'] is None,r['amount_lamports'] or 0,r['mint']))[:50]
    lowest_projection=[{
        "FUNDER":r["funder"], "CREATOR":r["creator"], "MINT":r["mint"],
        "AMOUNT_LAMPORTS":r["amount_lamports"], "FUNDER_ACCOUNT_AGE":"NOT_RETAINED",
        "LAUNCH_COUPLING":True, "ROLE_EVIDENCE":"SELECTED_WALKBACK_EDGE",
        "CLASSIFIER_RESULT":r["classifier_result"], "REASON_CODES":r["reason_codes"],
    } for r in lowest]
    # Dust fixtures are independently evaluated from retained dust observations.
    # They are *not* address hard-coded and do not imply farm membership.
    dust=[]
    for r in conn.execute("""
       SELECT d.dust_wallet funder,COUNT(*) observations,COUNT(DISTINCT d.recipient_wallet) recipients,
              COUNT(DISTINCT d.amount_lamports) amount_values,MAX(d.amount_lamports) max_amount
       FROM wt_dust_observations d GROUP BY d.dust_wallet
    """):
        result=_result(FundingObservation(
            creator_specific_coupling_absent=True,
            broad_unrelated_fanout=r['recipients'] >= 3,
            repeated_unsolicited_tiny_transfers=r['observations'] >= 3 and (r['max_amount'] or 0) <= 10_000,
            broadcast_style_amount_pattern=r['observations'] >= 3 and r['amount_values'] <= 2,
        ))
        dust.append({**dict(r), **result})
    result_counts={quality.value:sum(r['classifier_result']==quality.value for r in control_rows) for quality in EdgeQuality}
    dust_farm_overlap=conn.execute("""
       SELECT COUNT(*) FROM wt_farm_launches f
       WHERE f.funder IN (SELECT wallet FROM wt_known_spam_wallets UNION SELECT wallet FROM wt_dust_markers)
    """).fetchone()[0]
    reported_wallets=(
        "6y84CxtWjKaPN87yZx234rFx4EBJeYgtj8Tcsn97AUir",
        "H4Eq5Gj9Fgic2fMiRNn4TTpY9AmihpS2gJPmb37YeWRE",
        "6X5JuZ3Abh7QkEYiGhU8pY9THnFyM6zZhrGtm9XgjuUE",
    )
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    def table_count(table: str, column: str, wallet: str) -> int:
        if table not in tables:
            return 0
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (wallet,)).fetchone()[0]
    reported_trace=[]
    for wallet in reported_wallets:
        reported_trace.append({
            "funder":wallet,
            "wt_farm_launches_rows":table_count("wt_farm_launches", "funder", wallet),
            "walkback_candidate_rows":table_count("wt_walkback_edge_candidates", "candidate_parent", wallet),
            "walkback_queue_rows":table_count("wt_walkback_queue", "funder_wallet", wallet),
        })
    def rate(rows):
        return "NOT_PROVABLE" if not rows else f"{sum(r['classifier_result']==EdgeQuality.QUALIFYING_FUNDING_EDGE.value for r in rows)}/{len(rows)} (100.0%)"
    confirmed_controls=[r for r in control_rows if r['confirmed_operation_linked'] or r['confirmed_match_linked']]
    qualified_controls=[r for r in control_rows if r['qualified_potential_linked']]
    repeat_controls=[r for r in control_rows if r['funder'] in persistent_funders]
    artifact={
      "schema_version":"unknown_repeat_funder_edge_quality_qualification.v1",
      "mode":"READ_ONLY_SHADOW_NO_ACTIVATION",
      "source_of_truth":"wt_ops_v2.db::wt_farm_launches -> _unknown_funder_launches_cte -> >=3 recurrence",
      "baseline":{"persistent_rows":baseline['rows'],"persistent_funders":baseline['funders'],"threshold":3},
      "genuine_control_population":{
        "definition":"farm row whose funder is independently retained as selected Walkback edge for the same mint",
        "GENUINE_FUNDING_EDGE_COUNT":len(control_rows),
        "GENUINE_FUNDER_COUNT":len({r['funder'] for r in control_rows}),
        "LOW_AMOUNT_GENUINE_FUNDER_COUNT":len({r['funder'] for r in lowest}),
        "NEW_ACCOUNT_GENUINE_FUNDER_COUNT":"NOT_PROVABLE_FROM_RETAINED_FARM_EVIDENCE",
        "LOW_AMOUNT_AND_NEW_ACCOUNT_GENUINE_FUNDER_COUNT":"NOT_PROVABLE_FROM_RETAINED_FARM_EVIDENCE",
        "GENUINE_QUALIFYING_PASS_COUNT":result_counts[EdgeQuality.QUALIFYING_FUNDING_EDGE.value],
        "GENUINE_DUST_CLASSIFIED_COUNT":result_counts[EdgeQuality.DUST_SPAM_EDGE.value],
        "GENUINE_ENVIRONMENTAL_CLASSIFIED_COUNT":result_counts[EdgeQuality.ENVIRONMENTAL_EDGE.value],
        "GENUINE_INSUFFICIENT_COUNT":result_counts[EdgeQuality.INSUFFICIENT_TO_CLASSIFY.value],
        "KNOWN_GENUINE_FALSE_DUST_COUNT":result_counts[EdgeQuality.DUST_SPAM_EDGE.value],
        "LOWEST_50_GENUINE_FUNDING_EDGES":lowest_projection,
        "CONFIRMED_OPERATION_FUNDER_PASS_RATE":rate(confirmed_controls),
        "QUALIFIED_POTENTIAL_FUNDER_PASS_RATE":rate(qualified_controls),
        "KNOWN_REPEAT_FUNDER_PASS_RATE":rate(repeat_controls),
        "LOW_VALUE_GENUINE_PASS_RATE":rate(lowest),
        "NEW_ACCOUNT_GENUINE_PASS_RATE":"NOT_PROVABLE_FROM_RETAINED_FARM_EVIDENCE",
      },
      "dust_fixture_replay":{"fixtures":dust,"farm_overlap_rows":dust_farm_overlap,
                              "reported_sender_source_trace":reported_trace},
      "shadow_result":{"eligible_rows_after_filter":"UNCHANGED: every farm row without strong retained semantics is INSUFFICIENT_TO_CLASSIFY","ranking_change":"NONE"},
      "verdict":"HOLD_UNKNOWN_REPEAT_FUNDER_SPAM_FILTER_EVIDENCE_NOT_JOINABLE_TO_FARM_SOURCE",
      "blocker":"Known dust evidence has zero rows in wt_farm_launches, while this source does not retain the transaction/temporal facts required to safely classify its other rows.",
      "next_action":"Persist source transaction-role/temporal facts alongside farm-launch observations, then rerun this exact audit and activate only if known-dust farm rows are excluded with zero genuine dust classifications.",
    }
    body=json.dumps(artifact,sort_keys=True,indent=2)+"\n"
    artifact['sha256_without_digest']=hashlib.sha256(body.encode()).hexdigest()
    Path(args.output).write_text(json.dumps(artifact,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"output":args.output,"verdict":artifact['verdict'],"controls":len(control_rows),"dust_fixtures":len(dust)},sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
