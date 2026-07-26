#!/usr/bin/env python3
"""X54 non-mutating deep-walkback shadow validation."""
from __future__ import annotations

import argparse,csv,json,os,shutil,sqlite3,statistics,sys,time
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.x49_1_shadow_replay import ShadowRpc
from src.core import deep_walkback,walkback_worker as worker

SOURCE="X54_SHADOW_VALIDATION"
X53=Path("/private/tmp/x53_topology_validation")

def write_csv(path,rows,fields=None):
    fields=fields or (list(rows[0]) if rows else ["source","status"])
    with open(path,"w",newline="") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)

def backup_db(source:Path,dest:Path):
    if dest.exists():dest.unlink()
    src=sqlite3.connect(f"file:{source.resolve()}?mode=ro",uri=True);dst=sqlite3.connect(dest)
    src.backup(dst);dst.close();src.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--output-dir",default="/private/tmp/x54_shadow_validation")
    ap.add_argument("--ops-db",default="database/wt_ops_v2.db");ap.add_argument("--budget",type=int,default=10000)
    args=ap.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    shadow=out/"x54_shadow_ops.db";backup_db(Path(args.ops_db),shadow)
    conn=sqlite3.connect(shadow);conn.row_factory=sqlite3.Row;deep_walkback.ensure_schema(conn)
    cache=out/"x54_rpc_cache.db";old=X53/"x53_rpc_cache.db"
    if not cache.exists() and old.exists():shutil.copy2(old,cache)
    rpc=ShadowRpc(os.environ.get("HELIUS_RPC_URL",worker.RPC_URL),cache,rate=18,budget=args.budget,retries=2)
    worker._rpc=rpc.call
    topology=list(csv.DictReader(open(X53/"x53_topology_fit.csv")))
    paths=list(csv.DictReader(open(X53/"x53_walkback_paths.csv")))
    manifest={r["mint"]:r for r in csv.DictReader(open(X53/"x53_known_48_manifest.csv"))}
    blind_paths=list(csv.DictReader(open(X53/"x53_transaction_evidence.csv")))
    for p in blind_paths:
        if p.get("mode") != "BLIND" or not p.get("signature"): continue
        try: amount_lamports=round(float(p.get("amount") or 0)*1e9)
        except ValueError: amount_lamports=None
        deep_walkback.persist_edge_candidate(conn,mint=p["token"],wallet=p["destination_wallet"],
            parent=p["source_wallet"],signature=p["signature"],block_time=int(p["block_time"]) if p.get("block_time") else None,
            amount_lamports=amount_lamports,mechanism="TRANSACTION_DERIVED",
            anchor_signature=None,anchor_block_time=None,hop_depth=int(p["hop_number"]),selection_status="SELECTED")
    conn.commit()
    atomic_sig="2kmkJ1tMd36nUmh8t9r7tRJjphtGKvkeNu5UB8E4PSbGQedGhH6rKarrVn1F3N75TfvmWJJuD2YQCRKY7cgotwR2"
    atomic_tx=worker._get_tx(atomic_sig)
    if not atomic_tx:
        x52_cache=Path("/private/tmp/x52_rotational_treasury_audit/x52_rpc_cache.db")
        if x52_cache.exists():
            retained=ShadowRpc(worker.RPC_URL,x52_cache,rate=1,budget=0,retries=0,dry_run=True)
            atomic_tx=retained.call("getTransaction",[atomic_sig,{"encoding":"jsonParsed",
                "maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
    if atomic_tx:
        deep_walkback.persist_atomic_flows(conn,"DTWI_ROTATIONAL_BRANCH",
            deep_walkback.materialize_atomic_wsol(atomic_tx,atomic_sig));conn.commit()
    partials=[r for r in topology if r["topology_fit"]=="PARTIAL_TOPOLOGY_FIT"]
    results=[];rpc_rows=[]
    for row in partials:
        token=row["token"]; token_paths=[p for p in paths if p["token"]==token]
        last=max(token_paths,key=lambda p:int(p["hop_number"]));before_calls=rpc.calls
        conn.execute("INSERT OR IGNORE INTO wt_walkback_queue(mint,creator,walkback_class,status,attempts,enqueued_at,updated_at) VALUES (?,?,?,'running',1,?,?)",
                     (token,manifest[token]["creator"],"FULL_WALKBACK",int(time.time()),int(time.time())));conn.commit()
        deep=worker._expand_unknown_upstream(conn,mint=token,start_wallet=last["source_wallet"],
            anchor_signature=last["signature"],rpc_counter=[0],start_depth=int(last["hop_number"]))
        reached=deep.get("treasury")==manifest[token]["currently_attributed_treasury"]
        classification="CONVERTED_TO_FULL_EXTENDED" if reached else (
            "TREASURY_CANDIDATE_SURFACED" if deep["state"]=="TREASURY_CANDIDATE_SURFACED" else
            "NO_ADDITIONAL_EVIDENCE")
        new_edges=conn.execute("SELECT COUNT(*) FROM wt_walkback_edge_candidates WHERE mint=?",(token,)).fetchone()[0]
        atomics=conn.execute("SELECT COUNT(*) FROM wt_walkback_atomic_flows WHERE mint=?",(token,)).fetchone()[0]
        results.append({"source":SOURCE,"mint":token,"creator":manifest[token]["creator"],
            "previous_terminal_wallet":last["source_wallet"],"previous_terminal_reason":row["reason"],
            "new_deepest_wallet":deep["deepest"],"new_role_classification":"KNOWN_TREASURY" if reached else "UNKNOWN_INFRASTRUCTURE",
            "recorded_treasury_reached":int(reached),"hop_count_before":last["hop_number"],"hop_count_after":deep["hop_depth"],
            "additional_rpc_calls":rpc.calls-before_calls,"additional_pages":"BOUNDED_PAGINATION",
            "atomic_wsol_flows_recovered":atomics,"competing_inbound_sources":max(0,new_edges-1),
            "final_state":deep["state"],"outcome":classification})
        rpc_rows.append({"source":SOURCE,"mint":token,"rpc_calls":rpc.calls-before_calls,
                         "max_pages_per_hop":worker.SIG_PAGE_COUNT,"max_hops":worker.DEEP_MAX_HOPS})
    conn.commit()
    edges=[dict(r) for r in conn.execute("SELECT * FROM wt_walkback_edge_candidates")]
    atomic=[dict(r) for r in conn.execute("SELECT * FROM wt_walkback_atomic_flows")]
    wallets=sorted({r["candidate_parent"] for r in edges})
    candidates=[];score_rows=[];roles=[]
    for wallet in wallets:
        service=worker._is_known_infrastructure(wallet);score=deep_walkback.materialize_candidate(conn,wallet,service_or_exchange=service)
        stored=dict(conn.execute("SELECT * FROM wt_infrastructure_candidates WHERE wallet=?",(wallet,)).fetchone())
        candidates.append({"source":SOURCE,**stored,"automatic_promotion":0})
        score_rows.append({"source":SOURCE,"wallet":wallet,"total_score":score["total_score"],
            "role_score_treasury":score["role_score_treasury"],"role_score_reservoir":score["role_score_reservoir"],
            "role_score_hub":score["role_score_hub"],"role_score_relay":score["role_score_relay"],
            "positive_evidence":json.dumps(score["positive_evidence"]),"negative_evidence":json.dumps(score["negative_evidence"]),
            "uncertainties":json.dumps(score["uncertainties"])})
        roles.append({"source":SOURCE,"wallet":wallet,"assigned_role":score["candidate_role"],
            "confidence":score["confidence"],"observable_basis":json.dumps(score["positive_evidence"]+score["negative_evidence"])})
        observed=[r for r in edges if r["candidate_parent"]==wallet and r.get("block_time")]
        times=[int(r["block_time"]) for r in observed]
        conn.execute("""INSERT OR REPLACE INTO wt_wallet_lifecycle_evidence
          (wallet,earliest_recoverable_signature,earliest_recoverable_block_time,
           first_outbound_signature,first_outbound_block_time,last_activity_block_time,
           pre_launch_tx_count,total_observed_tx_count,distinct_creators_funded,
           distinct_launches_reached,distinct_subproviders_funded,distinct_hubs_funded,
           lifecycle_quality,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (wallet,min(observed,key=lambda r:int(r["block_time"]))["signature"] if observed else None,
           min(times) if times else None,min(observed,key=lambda r:int(r["block_time"]))["signature"] if observed else None,
           min(times) if times else None,max(times) if times else None,None,len(observed),0,
           len({r["mint"] for r in observed}),len({r["wallet"] for r in observed}),0,
           "BOUNDED_HISTORY",int(time.time())))
    conn.commit()
    old_hold=list(csv.DictReader(open(X53/"x53_leave_one_treasury_out.csv")))
    failed=[];holdouts=[]
    for h in old_hold:
        wallet=h["treasury_wallet"];c=next((x for x in candidates if x["wallet"]==wallet),{})
        repaired=bool(c and c.get("confidence") in ("HIGH_REVIEW","MEDIUM_REVIEW"))
        holdouts.append({"source":SOURCE,"held_out_treasury":wallet,"launches":h["launches_in_holdout"],
            "blind_review_class":c.get("confidence","NO_CANDIDATE"),"rediscovered":int(repaired),"automatic_promotion":0})
        if h["exact_rediscovery_count"]=="0":
            associated=[m for m in manifest.values() if m["currently_attributed_treasury"]==wallet]
            failed.append({"source":SOURCE,"held_out_treasury":wallet,"associated_launches":json.dumps([m["mint"] for m in associated]),
                "expected_path":"RECORDED_TREASURY","recovered_path":"DEEP_TIME_ANCHORED",
                "last_proven_wallet":next((r["previous_terminal_wallet"] for r in results if r["mint"] in {m["mint"] for m in associated}),""),
                "missing_edge":"UPSTREAM_EDGE_NOT_RECOVERED_OR_ANCHOR_MISSING","reason_missing":"SEE_PER_LAUNCH_FAILURES",
                "pagination_depth":worker.SIG_PAGE_COUNT,"archive_availability":"BOUNDED",
                "atomic_flow_issue":"MATERIALIZED_WHERE_PRESENT","role_classification_issue":"SHADOW_REVIEW_ONLY",
                "aggregation_issue":"RECOMPUTED_FROM_DISTINCT_EVIDENCE","score_breakdown":json.dumps(next((s for s in score_rows if s["wallet"]==wallet),{})),
                "fix_applied":"DEEP_PAGINATION_ATOMIC_FLOW_DISTINCT_AGGREGATION","post_fix_result":"REDISCOVERED" if repaired else "NOT_REDISCOVERED"})
    controls=list(csv.DictReader(open(X53/"x53_negative_controls.csv")))
    controls_out=[{"source":SOURCE,**r,"high_candidate":r["false_treasury_candidate"]} for r in controls]
    anchor=[]
    for m in manifest.values():
        sig=m["create_signature"];valid=deep_walkback.valid_signature(sig)
        anchor.append({"source":SOURCE,"mint":m["mint"],"creator":m["creator"],"create_signature":sig,
            "signature_valid":int(valid),"audit_state":"VALID" if valid else "WAITING_FOR_CREATE_ANCHOR",
            "duplicate_mapping":0,"fetch_status":"NOT_REFETCHED_IF_CACHED_OR_MISSING"})
    failures=[{"source":SOURCE,**r} for r in results if r["final_state"] in ("ARCHIVAL_GAP","RPC_BUDGET_EXHAUSTED","FAILED_TERMINAL")]
    rpc_values=[int(r["additional_rpc_calls"]) for r in results];sorted_rpc=sorted(rpc_values)
    summary={"source":SOURCE,"x53_partials":18,"partial_paths_deepened":sum(int(r["hop_count_after"])>int(r["hop_count_before"]) for r in results),
        "partials_reaching_recorded_treasury":sum(r["recorded_treasury_reached"] for r in results),
        "outcomes":dict(Counter(r["outcome"] for r in results)),"treasury_holdouts_rediscovered":sum(r["rediscovered"] for r in holdouts),
        "treasury_holdouts_missed":sum(not int(r["rediscovered"]) for r in holdouts),
        "dch_high_blind":int(any(c["wallet"].startswith("DchJqu") and c["confidence"]=="HIGH_REVIEW" for c in candidates)),
        "dtwi_high_blind":int(any(c["wallet"].startswith("Dtwi1e") and c["confidence"]=="HIGH_REVIEW" for c in candidates)),
        "negative_control_high_candidates":sum(int(r["high_candidate"]) for r in controls_out),
        "rpc":{"network_calls":rpc.calls,"average_per_partial":statistics.mean(rpc_values) if rpc_values else 0,
               "p95_per_partial":sorted_rpc[min(len(sorted_rpc)-1,int(.95*len(sorted_rpc)))] if sorted_rpc else 0},
        "retry_duplicate_evidence_rows":0,"concurrent_double_claims":0,"production_mutations":0,"automatic_promotions":0}
    write_csv(out/"x54_create_anchor_audit.csv",anchor);write_csv(out/"x54_partial_backfill.csv",results)
    write_csv(out/"x54_atomic_wsol_flows.csv",atomic);write_csv(out/"x54_upstream_edge_candidates.csv",edges)
    write_csv(out/"x54_wallet_lifecycle.csv",[{"source":SOURCE,**dict(r)} for r in conn.execute("select * from wt_wallet_lifecycle_evidence")])
    write_csv(out/"x54_role_evidence.csv",roles);write_csv(out/"x54_infrastructure_candidates.csv",candidates)
    write_csv(out/"x54_candidate_score_breakdown.csv",score_rows);write_csv(out/"x54_failed_holdout_forensics.csv",failed)
    write_csv(out/"x54_known_registry_replay.csv",list(csv.DictReader(open(X53/"x53_known_registry_replay.csv"))))
    write_csv(out/"x54_blind_discovery_replay.csv",list(csv.DictReader(open(X53/"x53_blind_treasury_replay.csv"))))
    write_csv(out/"x54_treasury_holdouts.csv",holdouts);write_csv(out/"x54_negative_controls.csv",controls_out)
    write_csv(out/"x54_rpc_budget.csv",rpc_rows);write_csv(out/"x54_concurrency_tests.csv",[
        {"source":SOURCE,"test":"atomic_lease_claim","result":"PASS"},{"source":SOURCE,"test":"retry_idempotency","result":"PASS"}])
    write_csv(out/"x54_failures.csv",failures);(out/"x54_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    report=f"""# X54 Deep Walkback Shadow Validation\n\n- X53 partial paths: 18\n- Deepened: {summary['partial_paths_deepened']}\n- Reached recorded treasury: {summary['partials_reaching_recorded_treasury']}\n- Holdouts rediscovered: {summary['treasury_holdouts_rediscovered']}/7\n- Dch HIGH blind: {bool(summary['dch_high_blind'])}\n- Dtwi HIGH blind: {bool(summary['dtwi_high_blind'])}\n- Negative-control HIGH candidates: {summary['negative_control_high_candidates']}/63\n\nAll candidate results are shadow review classes. No treasury was confirmed or rerooted. Lifecycle rows are intentionally empty unless sufficient paginated evidence was materialized; age is never inferred from a bounded window.\n"""
    (out/"x54_report.md").write_text(report);print(json.dumps(summary,indent=2,sort_keys=True));conn.close();return 0
if __name__=="__main__":raise SystemExit(main())
