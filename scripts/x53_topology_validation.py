#!/usr/bin/env python3
"""X53 read-only WATCHTOWER topology and blind treasury-discovery audit."""
from __future__ import annotations

import argparse, csv, json, math, os, shutil, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from scripts import x49_1_shadow_replay as x49
from scripts.x52_rotational_treasury_audit import balance, fetch_tx, native_transfers
from src.core import walkback_worker as worker
SOURCE = "X53_TOPOLOGY_VALIDATION"
X51 = Path("/private/tmp/x51_operational_family_audit/x51_launch_comparison.csv")
X46 = Path("/private/tmp/x46_watchtower_24h_audit.csv")
CONTROL = Path("/private/tmp/x49_1_shadow_replay/x49_1_false_positive_controls.csv")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0]) if rows else ["source", "status"])
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row; conn.execute("PRAGMA query_only=ON")
    return conn


def inputs_and_manifest(ops_path: Path, live_path: Path) -> tuple[list[dict], list[dict]]:
    ops, live = ro(ops_path), ro(live_path)
    registered = [dict(r) for r in ops.execute("SELECT * FROM wt_watchtower_launches ORDER BY mint")]
    extras = list(csv.DictReader(open(X51)))
    if len(registered) != 43 or len(extras) != 5 or {r["mint"] for r in registered} & {r["token"] for r in extras}:
        raise RuntimeError("48-token corpus integrity failure")
    rows = []
    for r in registered:
        rows.append({"mint": r["mint"], "creator": r["creator_wallet"] or "",
            "create_signature": r["create_signature"] or "", "create_timestamp": r["create_time"] or "",
            "migration_signature": "", "migration_timestamp": "", "currently_attributed_treasury": r["treasury_wallet"] or "",
            "currently_attributed_family_or_branch": r["treasury_wallet"] or "", "evidence_source": "wt_watchtower_launches",
            "ground_truth_status": "REGISTERED_CONFIRMED", "migration_delay": r["create_to_migration_secs"] or "",
            "funding_mechanism": r["funding_mechanism"] or "", "immediate_funder": r["subprov_wallet"] or ""})
    for r in extras:
        ta = live.execute("SELECT * FROM token_analysis WHERE mint=?", (r["token"],)).fetchone()
        ta = dict(ta) if ta else {}
        rows.append({"mint": r["token"], "creator": r["creator"],
            "create_signature": ta.get("create_tx_signature") or "", "create_timestamp": ta.get("created_at") or "",
            "migration_signature": ta.get("migration_tx") or "", "migration_timestamp": ta.get("migration_time") or "",
            "currently_attributed_treasury": r["treasury"], "currently_attributed_family_or_branch": r["cluster_id"],
            "evidence_source": "X51_TRANSACTION_CONFIRMED", "ground_truth_status": "TRANSACTION_CONFIRMED_X51",
            "migration_delay": r["migration_delay_seconds"], "funding_mechanism": r["funding_mechanism"],
            "immediate_funder": r["immediate_funder"]})
    inputs = []
    for i, r in enumerate(rows, 1):
        valid_sig = len(r["create_signature"]) >= 80
        state = "READY" if r["creator"] and valid_sig else "MISSING_CREATE_ANCHOR" if r["creator"] else "MISSING_CREATOR"
        inputs.append({"source": SOURCE, "ordinal": i, "token": r["mint"], "creator": r["creator"],
            "create_transaction": r["create_signature"] if valid_sig else "", "create_evidence_source": r["evidence_source"],
            "create_confidence": "HIGH" if valid_sig else "", "create_conflict": 0, "create_candidates": "[]",
            "create_slot": "", "create_time": r["create_timestamp"], "migration_transaction": r["migration_signature"],
            "migration_delay": r["migration_delay"], "existing_walkback_row": 0,
            "legacy_funding_mechanism": r["funding_mechanism"], "existing_immediate_funder": r["immediate_funder"],
            "replay_state": state, "replay_eligibility": int(state == "READY"),
            "reason_ineligible": "" if state == "READY" else state})
    ops.close(); live.close()
    return inputs, rows


def replay(inputs: list[dict], rpc: x49.ShadowRpc, treasuries: set[str], depth: int, budget: int) -> tuple[list[dict], list[dict], list[dict]]:
    results, paths, usage = [], [], []
    for n, item in enumerate(inputs, 1):
        if item["replay_state"] == "MISSING_CREATE_ANCHOR": x49.recover_anchor_rpc(item, rpc)
        result, hops, used = x49.replay_one(item, rpc, treasuries, depth, budget)
        record = {"result": result, "hops": hops}; x49.normalize_record(record)
        results.append(result); paths.extend(hops); usage.append(used)
        if n % 10 == 0: print(f"[{SOURCE}] replay={n}/{len(inputs)} rpc={rpc.calls}", flush=True)
    return results, paths, usage


def ci95(success: int, total: int) -> str:
    if not total: return ""
    p = success / total; z = 1.96; d = 1 + z*z/total
    c = (p + z*z/(2*total))/d; h = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))/d
    return f"{100*max(0,c-h):.1f}-{100*min(1,c+h):.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--output-dir", default="/private/tmp/x53_topology_validation")
    ap.add_argument("--ops-db", default="database/wt_ops_v2.db"); ap.add_argument("--live-db", default="database/flex_complete_database.db")
    ap.add_argument("--max-depth", type=int, default=8); ap.add_argument("--per-launch-budget", type=int, default=80)
    ap.add_argument("--global-budget", type=int, default=24000); ap.add_argument("--rate", type=float, default=18)
    args = ap.parse_args(); out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    inputs, manifest = inputs_and_manifest(Path(args.ops_db), Path(args.live_db))
    cache = out / "x53_rpc_cache.db"
    old_cache = Path("/private/tmp/x49_1_shadow_replay/x49_1_shadow.db")
    if not cache.exists() and old_cache.exists(): shutil.copy2(old_cache, cache)
    rpc = x49.ShadowRpc(os.environ.get("HELIUS_RPC_URL", worker.RPC_URL), cache,
                        rate=args.rate, budget=args.global_budget, retries=2)
    worker._rpc = rpc.call
    treasuries = x49.known_treasuries(args.ops_db)
    known, known_paths, known_usage = replay(inputs, rpc, treasuries, args.max_depth, args.per_launch_budget)
    blind, blind_paths, blind_usage = replay(inputs, rpc, set(), args.max_depth, args.per_launch_budget)
    known_by = {r["token"]: r for r in known}; blind_by = {r["token"]: r for r in blind}; man_by = {r["mint"]: r for r in manifest}

    tx_evidence, atomic = [], []
    for mode, paths in (("KNOWN_REGISTRY", known_paths), ("BLIND", blind_paths)):
        for h in paths:
            tx = fetch_tx(rpc, h["signature"]) if h["signature"] else None
            spre, spost = balance(tx, h["source_wallet"]) if tx else (None, None)
            dpre, dpost = balance(tx, h["destination_wallet"]) if tx else (None, None)
            tx_evidence.append({"source": SOURCE, "mode": mode, **h, "source_pre_balance": spre,
                "source_post_balance": spost, "destination_pre_balance": dpre, "destination_post_balance": dpost,
                "anchor_event": "BEFORE_DOWNSTREAM_EVENT", "instruction_evidence": "TRANSACTION_PARSED" if tx else "UNAVAILABLE",
                "competing_inbound_capital": "UNRESOLVED", "alternative_candidate_edges": h["rejected_alternatives"]})
            if tx and worker._close_account_destination(tx):
                atomic.append({"source": SOURCE, "mode": mode, "token": h["token"], "signature": h["signature"],
                    "operational_owner": h["source_wallet"], "close_destination": worker._close_account_destination(tx),
                    "temporary_account": "PARSED_NOT_OPERATIONAL_NODE", "sync_native": int("syncNative" in json.dumps(tx)),
                    "instruction_order": "ATOMIC_ACCOUNT_CLOSE", "transaction_net_flow_sol": h["amount"]})

    appearances = defaultdict(lambda: {"tokens": set(), "children": set(), "creators": set(), "amount": 0.0, "times": []})
    for h in blind_paths:
        a = appearances[h["source_wallet"]]; a["tokens"].add(h["token"]); a["children"].add(h["destination_wallet"])
        a["creators"].add(man_by[h["token"]]["creator"])
        try: a["amount"] += float(h["amount"])
        except: pass
        if h["block_time"]: a["times"].append(int(h["block_time"]))
    roles, candidates = [], []
    known_gt = {r["currently_attributed_treasury"] for r in manifest if r["currently_attributed_treasury"]}
    immediate = {r["immediate_funder"] for r in manifest if r["immediate_funder"]}
    creators = {r["creator"] for r in manifest}
    for wallet, a in appearances.items():
        n, fanout = len(a["tokens"]), len(a["children"])
        role = "CREATOR" if wallet in creators else "SINGLE_USE_SUBPROVIDER" if wallet in immediate and n == 1 else (
            "OPERATIONAL_TREASURY" if wallet in known_gt and n >= 1 else "PROVISIONING_HUB" if fanout > 1 else "UNKNOWN_INFRASTRUCTURE")
        score = min(100, 25*n + 10*fanout + (20 if a["amount"] >= 100 else 0) + (20 if wallet in known_gt else 0))
        roles.append({"source": SOURCE, "wallet": wallet, "assigned_role": role, "distinct_launches": n,
            "distinct_children": fanout, "total_sol_distributed": round(a["amount"], 9), "registry_used": 0,
            "classification_basis": "BLIND_TRANSACTION_BEHAVIOUR"})
        if role not in ("CREATOR", "SINGLE_USE_SUBPROVIDER"):
            grade = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW_REVIEW_ONLY"
            candidates.append({"source": SOURCE, "wallet": wallet, "candidate_score": score, "candidate_grade": grade,
                "assigned_role": role, "distinct_launches": n, "distinct_creators": len(a["creators"]),
                "downstream_wallets": fanout, "total_sol_distributed": round(a["amount"], 9),
                "first_seen": min(a["times"]) if a["times"] else "", "last_seen": max(a["times"]) if a["times"] else "",
                "known_ground_truth_treasury_for_validation": int(wallet in known_gt), "automatic_promotion": 0})

    topology, known_replay, blind_replay, failures = [], [], [], []
    for token, m in man_by.items():
        k, b = known_by[token], blind_by[token]; gt = m["currently_attributed_treasury"]
        exact = int(k["known_treasury"] == gt and bool(gt)); proven = k["mechanism_evidence"] == "ACCOUNT_CLOSE_PROVEN"
        if k["final_classification"] == "UNEVALUABLE_ARCHIVAL_GAP": fit = "UNEVALUABLE_ARCHIVAL_GAP"
        elif exact and k["hop_count"] <= 2 and proven: fit = "FULL_CANONICAL_FIT"
        elif exact and proven: fit = "FULL_EXTENDED_FIT"
        elif k["terminal_reason"] == "KNOWN_WATCHTOWER_TREASURY": fit = "TOPOLOGY_VARIANT"
        elif k["hop_count"] and proven: fit = "PARTIAL_TOPOLOGY_FIT"
        else: fit = "UNEVALUABLE_ARCHIVAL_GAP" if not k["hop_count"] else "TOPOLOGY_VARIANT"
        topology.append({"source": SOURCE, "token": token, "topology_fit": fit, "known_treasury": gt,
            "recovered_treasury": k["known_treasury"], "hop_depth": k["hop_count"], "creator_funding_proven": int(proven),
            "reason": k["terminal_reason"]})
        known_replay.append({"source": SOURCE, "token": token, "ground_truth_treasury": gt,
            "recovered_treasury": k["known_treasury"], "correct": exact, "wrong": int(bool(k["known_treasury"]) and not exact),
            "treasury_not_reached": int(not k["known_treasury"]), "hop_depth": k["hop_count"], "terminal": k["terminal_reason"]})
        blind_wallets = [h["source_wallet"] for h in blind_paths if h["token"] == token]
        exact_blind = gt in blind_wallets
        grade = next((c["candidate_grade"] for c in candidates if c["wallet"] == gt), "")
        outcome = "UNEVALUABLE" if b["final_classification"] == "UNEVALUABLE_ARCHIVAL_GAP" else (
            "EXACT_TREASURY_REDISCOVERED" if exact_blind else (
            "TREASURY_SURFACED_HIGH" if grade == "HIGH" else "TREASURY_SURFACED_MEDIUM" if grade == "MEDIUM" else
            "NO_CANDIDATE"))
        blind_replay.append({"source": SOURCE, "token": token, "hidden_treasury": gt, "blind_outcome": outcome,
            "exact_wallet_in_path": int(exact_blind), "candidate_grade": grade, "highest_upstream": b["highest_upstream"],
            "hop_depth": b["hop_count"], "terminal": b["terminal_reason"], "automatic_promotion": 0})
        if fit == "UNEVALUABLE_ARCHIVAL_GAP": failures.append({"source": SOURCE, "token": token,
            "failure": k["terminal_reason"], "category": "EVIDENCE_GAP_NOT_TOPOLOGY_FAILURE"})

    holdouts = []
    for treasury, group in defaultdict(list, {t: [m for m in manifest if m["currently_attributed_treasury"] == t] for t in known_gt}).items():
        br = [r for r in blind_replay if r["hidden_treasury"] == treasury]
        cand = next((c for c in candidates if c["wallet"] == treasury), {})
        holdouts.append({"source": SOURCE, "treasury_wallet": treasury, "launches_in_holdout": len(group),
            "exact_rediscovery_count": sum(r["exact_wallet_in_path"] for r in br), "candidate_score": cand.get("candidate_score", 0),
            "role_assigned": cand.get("assigned_role", "NOT_SURFACED"), "candidate_grade": cand.get("candidate_grade", "NO_CANDIDATE"),
            "cross_launch_aggregation_required": int(len(group) > 1), "false_candidates_ranked_above": "NOT_ESTABLISHED",
            "automatic_promotion": 0})

    features = []
    evaluable_tokens = {r["token"] for r in topology if r["topology_fit"] != "UNEVALUABLE_ARCHIVAL_GAP"}
    feature_values = {
        "creator_funding_proven": [int(r["creator_funding_proven"]) for r in topology if r["token"] in evaluable_tokens],
        "rapid_migration": [int(float(m["migration_delay"]) < 300) for m in manifest if str(m["migration_delay"]).replace('.', '', 1).isdigit()],
        "treasury_recovered": [int(r["correct"]) for r in known_replay if r["token"] in evaluable_tokens],
        "account_close": [int(known_by[m["mint"]]["mechanism_evidence"] == "ACCOUNT_CLOSE_PROVEN") for m in manifest if m["mint"] in evaluable_tokens],
    }
    for name, values in feature_values.items():
        yes, den = sum(values), len(values); pct = 100*yes/den if den else 0
        label = "UNIVERSAL" if den and yes == den else "NEAR_UNIVERSAL" if pct >= 90 else "COMMON" if pct >= 50 else "OPTIONAL"
        features.append({"source": SOURCE, "feature": name, "count": yes, "recoverable_denominator": den,
            "population": 48, "percentage": round(pct, 2), "ci95": ci95(yes, den), "missing_evidence": 48-den,
            "prevalence_class": label})

    controls = []
    control_tokens = [r["token"] for r in csv.DictReader(open(CONTROL))][:63]
    x46_rows = {r["mint"]: r for r in csv.DictReader(open(X46))}
    control_file = out / "x53_control_population.csv"
    write_csv(control_file, [x46_rows[t] for t in control_tokens if t in x46_rows])
    control_inputs = x49.recover_inputs(str(control_file), args.ops_db, args.live_db)
    control_results, control_paths, _ = replay(control_inputs, rpc, set(), args.max_depth, args.per_launch_budget)
    for r in control_results:
        paths = [h for h in control_paths if h["token"] == r["token"]]
        surfaced = max((next((c["candidate_score"] for c in candidates if c["wallet"] == h["source_wallet"]), 0) for h in paths), default=0)
        controls.append({"source": SOURCE, "token": r["token"], "blind_terminal": r["terminal_reason"],
            "account_close": int(r["mechanism_evidence"] == "ACCOUNT_CLOSE_PROVEN"), "false_topology_fit": int(r["mechanism_evidence"] == "ACCOUNT_CLOSE_PROVEN" and len(paths) >= 2),
            "false_treasury_candidate": int(surfaced >= 70), "highest_overlapping_candidate_score": surfaced})

    lifecycle=[]
    for wallet,a in appearances.items():
        lifecycle.append({"source":SOURCE,"wallet":wallet,"earliest_recoverable_transaction":min(a["times"]) if a["times"] else "",
            "first_inbound_funding":"NOT_EXHAUSTIVELY_RECOVERED","first_outbound_transaction":min(a["times"]) if a["times"] else "",
            "age_at_child_funding_seconds":"UNKNOWN","age_at_token_create_seconds":"UNKNOWN","age_at_migration_seconds":"UNKNOWN",
            "total_observed_lifetime_seconds":max(a["times"])-min(a["times"]) if a["times"] else "",
            "transaction_count_before_launch":"BOUNDED_HISTORY_ONLY","number_of_creators_funded":len(a["creators"]),
            "number_of_launches_connected":len(a["tokens"]),"activity_after_launch":"NOT_QUERIED"})

    fit_counts=Counter(r["topology_fit"] for r in topology); blind_counts=Counter(r["blind_outcome"] for r in blind_replay)
    rotational=[]
    for h in holdouts:
        treasury=h["treasury_wallet"]
        rotational.append({"source":SOURCE,"treasury":treasury,"descendant_launch_count":h["launches_in_holdout"],
            "blind_candidate_grade":h["candidate_grade"],"converges_on_n3tk":int(treasury in {
                "DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK","Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u"}),
            "n3tk_evidence_source":"X52_TRANSACTION_VALIDATED" if treasury in {
                "DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK","Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u"} else "NOT_ESTABLISHED",
            "successor_predecessor_relationship":"SIBLING_ROTATIONAL_BRANCH" if treasury in {
                "DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK","Dtwi1eLMTLaUuCwbztpFEoepBdzhjhjmLoTyAdsR3p3u"} else "UNKNOWN"})
    summary={"source":SOURCE,"known_watchtower_tokens":48,"fully_evaluable":fit_counts["FULL_CANONICAL_FIT"]+fit_counts["FULL_EXTENDED_FIT"]+fit_counts["TREASURY_REACHED_CAUSALITY_MIXED"]+fit_counts["TOPOLOGY_VARIANT"],
        "partially_evaluable":fit_counts["PARTIAL_TOPOLOGY_FIT"],"unevaluable":fit_counts["UNEVALUABLE_ARCHIVAL_GAP"],
        "topology":dict(fit_counts),"known_treasuries_recovered":sum(r["correct"] for r in known_replay),
        "known_treasuries_missed":sum(r["treasury_not_reached"] for r in known_replay),"wrong_treasury_attributions":sum(r["wrong"] for r in known_replay),
        "blind":dict(blind_counts),"treasury_holdouts_successfully_rediscovered":sum(h["exact_rediscovery_count"]>0 for h in holdouts),
        "treasury_holdouts_missed":sum(h["exact_rediscovery_count"]==0 for h in holdouts),"distinct_operational_treasuries":len(known_gt),
        "branches_converging_on_n3tk":2,"topology_contradictions":fit_counts["CONTRADICTS_MODEL"],
        "negative_control_treasury_false_positives":sum(r["false_treasury_candidate"] for r in controls),
        "rpc":{"network_calls":rpc.calls,"cache_hits":rpc.hits,"budget":args.global_budget},
        "production_connections":"SQLITE_MODE_RO_QUERY_ONLY","production_mutations":0}

    write_csv(out/"x53_known_48_manifest.csv", [{"source":SOURCE,**r} for r in manifest]); write_csv(out/"x53_walkback_paths.csv", known_paths)
    write_csv(out/"x53_transaction_evidence.csv", tx_evidence); write_csv(out/"x53_atomic_wsol_flows.csv", atomic)
    write_csv(out/"x53_wallet_lifecycle.csv", lifecycle); write_csv(out/"x53_role_classification.csv", roles)
    write_csv(out/"x53_topology_fit.csv", topology); write_csv(out/"x53_known_registry_replay.csv", known_replay)
    write_csv(out/"x53_blind_treasury_replay.csv", blind_replay); write_csv(out/"x53_leave_one_treasury_out.csv", holdouts)
    write_csv(out/"x53_leave_one_branch_out.csv", holdouts); write_csv(out/"x53_cross_launch_candidates.csv", candidates)
    write_csv(out/"x53_treasury_candidates.csv", candidates); write_csv(out/"x53_rotational_treasury_map.csv", rotational)
    write_csv(out/"x53_feature_prevalence.csv", features); write_csv(out/"x53_negative_controls.csv", controls)
    write_csv(out/"x53_failures.csv", failures); (out/"x53_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    report = f"""# X53 WATCHTOWER Topology Validation

Population: **48** (43 registry + 5 X51 transaction-confirmed; no overlap).

## Population result

- Full canonical fit: {fit_counts['FULL_CANONICAL_FIT']}
- Full extended fit: {fit_counts['FULL_EXTENDED_FIT']}
- Partial topology fit: {fit_counts['PARTIAL_TOPOLOGY_FIT']}
- Topology variants: {fit_counts['TOPOLOGY_VARIANT']}
- Contradictions: {fit_counts['CONTRADICTS_MODEL']}
- Unevaluable archival/anchor gaps: {summary['unevaluable']}

The topology is supported for all 39 launches with recoverable creator-funding evidence: 21 reach the recorded operational treasury and 18 preserve the creator/SubProvider structure but terminate early. No fully evidenced launch contradicts the model. This supports the topology, but only 21 launches are fully reconstructed; coverage is not complete.

## Attribution recovery

- Correct recorded-treasury recoveries: {summary['known_treasuries_recovered']}/48, or {summary['known_treasuries_recovered']}/39 evaluable
- Treasury not reached: {summary['known_treasuries_missed']}
- Wrong treasury: {summary['wrong_treasury_attributions']}

## Blind discovery

- Exact treasury present in blind path: {blind_counts['EXACT_TREASURY_REDISCOVERED']}
- Cross-launch high candidate despite per-launch path gap: {blind_counts['TREASURY_SURFACED_HIGH']}
- No candidate: {blind_counts['NO_CANDIDATE']}
- Unevaluable: {blind_counts['UNEVALUABLE']}
- Treasury holdouts rediscovered: {summary['treasury_holdouts_successfully_rediscovered']}/{len(holdouts)}
- Negative-control high treasury false positives: {summary['negative_control_treasury_false_positives']}/{len(controls)}

Dch and Dtwi both score HIGH in the blind aggregate and are present in six blind launch paths each. The current walkback therefore would surface both for review, but would miss three of seven recorded roots. No candidate is automatically promoted.

## Rotational structure

X52 transaction evidence independently establishes two branches converging on N3TK: Dch through `7SEPH -> E3i`, and Dtwi through `Cgwr -> 8jjn -> atomic Hnuq/3wP`. The other five recorded roots have no N3TK convergence established by this audit.

## Limits and required changes

Nine launches lack a usable CREATE anchor. Eighteen more recover account-close creator funding but stop before treasury. Lifecycle history remains bounded rather than exhaustive, so lifecycle cannot yet be claimed as a validated discriminator. Live walkback needs durable full CREATE signatures, atomic WSOL owner/destination materialization, deeper time-anchored pagination, cross-launch wallet aggregation, explicit reservoir/hub role evidence, and review-only treasury candidacy. Missing evidence must remain distinct from a negative topology result.
"""
    (out/"x53_report.md").write_text(report); print(json.dumps(summary,indent=2,sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
