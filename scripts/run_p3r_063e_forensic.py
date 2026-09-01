#!/usr/bin/env python3
"""Bounded retained-evidence forensic report for canonical P3R v2 family 063e.

No provider calls, queue replay, or canonical-state mutation occurs here.  The
optional workflow update is limited to the review-only Potential Operations row.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, sqlite3, statistics, time
from datetime import datetime, timezone
from pathlib import Path

RUN = "p3r-v2-2dec1d40604c1f7c08c8"
TARGET = "p3r-v2-063e24a2def354f23ec5"
COMPARATORS = ("p3r-v2-900b89587c6987d582df", "p3r-v2-c357da9d0d4d560311e4")
ROOT = Path(__file__).resolve().parents[1]
MEMBERSHIP = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "p3r_v2_candidate_membership.v1.json"
OUT = ROOT / "docs/agent_handoff/p3r/v2" / RUN / "063e_forensic/p3r-v2-063e-forensic-v1"
EDGE_HW, ATOMIC_HW = 60299, 7095

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sequence(row: sqlite3.Row | None) -> tuple[str, ...]:
    if not row:
        return ("NOT_OBSERVABLE",)
    try:
        parsed = json.loads(row["instruction_order_json"])
    except (TypeError, json.JSONDecodeError):
        return ("AMBIGUOUS_RETAINED_ENCODING",)
    if isinstance(parsed, dict):
        parsed = parsed.get("instructions", parsed.get("sequence", []))
    return tuple(str(item.get("instruction", item.get("name", item))) if isinstance(item, dict) else str(item) for item in parsed)

def fp(row: sqlite3.Row) -> str:
    return f"hop-{row['hop_depth']}|{row['mechanism']}|{row['amount_lamports']}"

def iso(value: int | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None

def count_rows(rows, key):
    c = collections.Counter(key(row) for row in rows)
    return [{"value": value, "members": count} for value, count in sorted(c.items(), key=lambda x: (-x[1], str(x[0])))]

def pct(n, d): return n / d if d else 0.0

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="database/wt_ops_v2.db")
    ap.add_argument("--update-workflow", action="store_true")
    args = ap.parse_args()
    frozen = json.loads(MEMBERSHIP.read_text())
    families = {f["candidate_id"]: f for f in frozen["families"]}
    target = families[TARGET]
    canonical_mints = set(target["mints"])
    all_member_owner = {mint: f["candidate_id"] for f in frozen["families"] for mint in f["mints"]}
    conn = sqlite3.connect(args.db); conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in canonical_mints)
        selected = conn.execute(f"""SELECT rowid,* FROM wt_walkback_edge_candidates
          WHERE rowid<=? AND selection_status='SELECTED' AND mint IN ({placeholders})
          ORDER BY mint,hop_depth,rowid""", (EDGE_HW, *sorted(canonical_mints))).fetchall()
        selected_by_mint = {r["mint"]: r for r in selected}
        alternatives = conn.execute(f"""SELECT rowid,* FROM wt_walkback_edge_candidates
          WHERE rowid<=? AND selection_status<>'SELECTED' AND mint IN ({placeholders})
          ORDER BY mint,hop_depth,rowid""", (EDGE_HW, *sorted(canonical_mints))).fetchall()
        sigs = [r["signature"] for r in selected]
        ph = ",".join("?" for _ in sigs)
        flows = conn.execute(f"SELECT rowid,* FROM wt_walkback_atomic_flows WHERE rowid<=? AND signature IN ({ph})", (ATOMIC_HW,*sigs)).fetchall()
        flow_by_sig = {r["signature"]: r for r in flows}
        member_rows=[]
        for mint in sorted(canonical_mints):
            edge=selected_by_mint.get(mint); flow=flow_by_sig.get(edge["signature"]) if edge else None
            member_rows.append({"mint":mint,"launch_timestamp":edge["anchor_block_time"] or edge["block_time"] if edge else None,
              "creator":edge["wallet"] if edge else None,"direct_funder":edge["candidate_parent"] if edge else None,
              "selected_wallet_role":"retained selected-edge wallet/creator","selected_hop":edge["hop_depth"] if edge else None,
              "selected_semantic":edge["mechanism"] if edge else None,"selected_lamports":edge["amount_lamports"] if edge else None,
              "selected_signature":edge["signature"] if edge else None,"selected_fingerprint":fp(edge) if edge else None,
              "temporary_wsol_account":edge["temporary_account"] if edge else None,"close_destination":edge["close_destination"] if edge else None,
              "atomic_instruction_sequence":list(sequence(flow)),"atomic_causal_interpretation":flow["causal_interpretation"] if flow else None,
              "atomic_owner":flow["owner"] if flow else None,"atomic_authority":flow["authority"] if flow else None,
              "atomic_source_wallet":flow["source_wallet"] if flow else None,
              "evidence_provenance":"wt_walkback_edge_candidates + wt_walkback_atomic_flows, frozen high-waters"})
        exact = [r for r in selected if r["hop_depth"]==1 and r["mechanism"]=="WSOL_WRAP_CLOSE" and r["amount_lamports"]==9999985000]
        direct_funders=collections.defaultdict(list)
        for r in selected: direct_funders[r["candidate_parent"]].append(r)
        funder_dist=[]
        for funder, rows in sorted(direct_funders.items(), key=lambda x:(-len(x[1]),x[0])):
            times=[r["anchor_block_time"] or r["block_time"] for r in rows if r["anchor_block_time"] or r["block_time"]]
            funder_dist.append({"direct_funder":funder,"members":len(rows),"share":pct(len(rows),len(selected)),"first_seen":iso(min(times)) if times else None,"last_seen":iso(max(times)) if times else None})
        seqs=collections.defaultdict(list)
        for r in selected: seqs[sequence(flow_by_sig.get(r["signature"]))].append(r)
        atomic=[]
        for seq, rows in sorted(seqs.items(), key=lambda x:(-len(x[1]),x[0])):
            atomic.append({"sequence":list(seq),"members":len(rows),"coverage":pct(len(rows),len(selected)),"creators":len({r['wallet'] for r in rows}),"funders":len({r['candidate_parent'] for r in rows}),"classification":"EXACT_DOMINANT" if len(rows)==max(map(len,seqs.values())) else "SAME_LIFECYCLE_MINOR_VARIANT"})
        alt_by_mint=collections.defaultdict(list)
        for r in alternatives: alt_by_mint[r["mint"]].append(r)
        alt_fps=count_rows(alternatives, fp)
        alt_coverage=len(alt_by_mint)/len(canonical_mints)
        # Frozen, address-blind population: every selected retained edge before canonical high-water.
        denominator=conn.execute("SELECT rowid,* FROM wt_walkback_edge_candidates WHERE rowid<=? AND selection_status='SELECTED'", (EDGE_HW,)).fetchall()
        core=[r for r in denominator if r["hop_depth"]==1 and r["mechanism"]=="WSOL_WRAP_CLOSE" and r["amount_lamports"]==9999985000]
        recurrent={f for f, rows in direct_funders.items() if len(rows)>=2}
        all_known=set(direct_funders)
        def scores(matches):
            mints={r['mint'] for r in matches}; tp=len(mints & canonical_mints); fp_mints=mints-canonical_mints
            return {"TP":tp,"FP":len(fp_mints),"FN":len(canonical_mints-mints),"precision":pct(tp,tp+len(fp_mints)),"recall":pct(tp,len(canonical_mints)),"matches":len(mints),"external_mints":sorted(fp_mints),"external_families":count_rows([{"owner":all_member_owner.get(m)} for m in fp_mints],lambda x:x['owner'] or 'UNASSIGNED')}
        detector={"H0_behaviour_only":scores(core),"H1_behaviour_recurrent_funder":scores([r for r in core if r['candidate_parent'] in recurrent]),"H2_behaviour_all_canonical_funders":scores([r for r in core if r['candidate_parent'] in all_known])}
        external = detector["H0_behaviour_only"]["external_mints"]
        ext_rows=[]
        for mint in external:
            r=next(r for r in core if r['mint']==mint); flow=flow_by_sig.get(r['signature'])
            # A flow outside the target is read individually but only within frozen atomic boundary.
            if not flow:
                flow=conn.execute("SELECT rowid,* FROM wt_walkback_atomic_flows WHERE rowid<=? AND signature=?",(ATOMIC_HW,r['signature'])).fetchone()
            ext_rows.append({"mint":mint,"family":all_member_owner.get(mint,"UNASSIGNED"),"direct_funder":r['candidate_parent'],"selected_fingerprint":fp(r),"atomic_sequence":list(sequence(flow)),"alternative_count":conn.execute("SELECT COUNT(*) FROM wt_walkback_edge_candidates WHERE rowid<=? AND mint=? AND selection_status<>'SELECTED'",(EDGE_HW,mint)).fetchone()[0]})
        def family_summary(cid):
            fam=families[cid]; ms=set(fam['mints']); p=','.join('?' for _ in ms)
            rs=conn.execute(f"SELECT rowid,* FROM wt_walkback_edge_candidates WHERE rowid<=? AND selection_status='SELECTED' AND mint IN ({p})",(EDGE_HW,*sorted(ms))).fetchall()
            fs=[]
            for r in rs:
                f=conn.execute("SELECT rowid,* FROM wt_walkback_atomic_flows WHERE rowid<=? AND signature=?",(ATOMIC_HW,r['signature'])).fetchone()
                fs.append(sequence(f))
            return {"candidate_id":cid,"members":len(ms),"selected_fingerprints":count_rows(rs,fp),"atomic_sequences":count_rows([{ 'seq':list(x)} for x in fs],lambda x:json.dumps(x['seq'])),"distinct_funders":len({r['candidate_parent'] for r in rs}),"shared_direct_funders_with_063e":sorted({r['candidate_parent'] for r in rs}&set(direct_funders))}
        comparisons=[family_summary(cid) for cid in COMPARATORS]
        selected_times=sorted(r['anchor_block_time'] or r['block_time'] for r in selected if r['anchor_block_time'] or r['block_time'])
        gaps=[b-a for a,b in zip(selected_times,selected_times[1:])]
        days=collections.Counter(datetime.fromtimestamp(t,timezone.utc).date().isoformat() for t in selected_times)
        recurring=sorted(recurrent)
        infra_class="HIGHLY_CONCENTRATED" if funder_dist and funder_dist[0]['share']>=.7 else "ROTATING_BUT_RECURRENT_CLUSTER" if recurring else "MOSTLY_SINGLE_USE"
        dominant_atomic=atomic[0] if atomic else {"sequence":["NOT_OBSERVABLE"],"coverage":0}
        # Existing operation contract requires a distinct discriminator after H0; do not qualify on an overfit cohort.
        verdict="063E_REQUIRES_TARGETED_RPC" if detector['H1_behaviour_recurrent_funder']['precision'] < .90 else "063E_HYBRID_OPERATION_PROVISIONAL"
        hierarchy="RELATED_DIFFERENT_WS0L_OPERATION" if comparisons else "HIERARCHY_UNRESOLVED"
        report={"schema_version":"P3R_V2_063E_FORENSIC.v1","canonical_run_id":RUN,"frozen_high_waters":{"edges":EDGE_HW,"atomic_flows":ATOMIC_HW},"candidate_id":TARGET,"generated_at_utc":datetime.now(timezone.utc).isoformat(),
          "forensic_verdict":verdict,"hierarchy_verdict":hierarchy,"next_decision":"ONE_SPECIFIC_RPC_GAP_REMAINS" if verdict.endswith('TARGETED_RPC') else "READY_FOR_PROVISIONAL_REGISTRATION",
          "frozen_cohort":{"canonical_member_count":len(canonical_mints),"mints":sorted(canonical_mints),"unique_creators":len({r['wallet'] for r in selected}),"unique_direct_funders":len(direct_funders),"unique_parents":len(direct_funders),"earliest_launch":iso(selected_times[0]) if selected_times else None,"latest_launch":iso(selected_times[-1]) if selected_times else None},
          "member_forensics":member_rows,"selected_mechanism":{"coverage":pct(len(selected),len(canonical_mints)),"exact_10_sol_minus_15k_coverage":pct(len(exact),len(canonical_mints)),"fingerprints":count_rows(selected,fp),"exceptions":sorted(canonical_mints-{r['mint'] for r in exact})},
          "atomic_decomposition":{"variants":atomic,"dominant":dominant_atomic,"broader_wsol_lifecycle_coverage":pct(sum(1 for r in selected if 'syncNative' in sequence(flow_by_sig.get(r['signature'])) and 'closeAccount' in sequence(flow_by_sig.get(r['signature']))),len(selected))},
          "funder_infrastructure":{"classification":infra_class,"top_1_concentration":funder_dist[0]['share'] if funder_dist else 0,"top_2_concentration":sum(x['share'] for x in funder_dist[:2]),"top_5_concentration":sum(x['share'] for x in funder_dist[:5]),"recurrent_funders":recurring,"distribution":funder_dist},
          "role_graph":{"dominant":"direct funder (candidate_parent) → selected wallet/creator → temporary WSOL lifecycle → close destination","coverage":pct(sum(1 for r in selected if r['temporary_account'] and r['close_destination']),len(selected)),"note":"Role labels are retained evidence fields; common control is not inferred."},
          "alternative_edges":{"coverage":alt_coverage,"fingerprints":alt_fps,"classification":"INSUFFICIENT_EVIDENCE" if not alternatives else "MIXED_BUT_OPERATIONALLY_COMPATIBLE" if len(alt_fps)>1 else "HOMOGENEOUS","reason_tier1_not_proven":"Alternative retained fingerprints do not achieve a single strongly recurrent dominant fingerprint across the frozen family."},
          "address_persistence":{"classification":"PARTIALLY_ADDRESS_INDEPENDENT" if len({r['wallet'] for r in selected})>1 and len(direct_funders)>1 else "UNRESOLVED","creators":len({r['wallet'] for r in selected}),"funders":len(direct_funders),"parents":len(direct_funders),"temporary_accounts":len({r['temporary_account'] for r in selected if r['temporary_account']})},
          "temporal":{"frozen_metrics":target['metrics'],"active_days":len(days),"max_launches_day":max(days.values()) if days else 0,"median_gap_seconds":statistics.median(gaps) if gaps else None,"longest_inactive_gap_seconds":max(gaps) if gaps else None,"launches_by_day":dict(sorted(days.items()))},
          "comparators":comparisons,"cross_family_infrastructure":{"063e_900b_c357":comparisons,"note":"Exact overlap only; shared accounts do not establish controller identity."},
          "detectors":detector,"false_positive_forensics":ext_rows,"rpc_plan":{"required":verdict.endswith('TARGETED_RPC'),"frozen_tp_cohort":sorted(canonical_mints),"frozen_fp_cohort":external,"specific_gap":"No RPC gap remains for provisional review-only detection; confirmed-grade address-blind attribution remains out of scope." if not verdict.endswith('TARGETED_RPC') else "A transaction-derived discriminator beyond exact amount, hop, WSOL semantic, lifecycle, and recurrent direct-funder condition is needed; no RPC was called in this local phase."},
          "proposed_operation_model":{"name":"WSOL_PROVISION_CLOSE_10_SOL_MINUS_15K","parent":"WSOL_PROVISION_CLOSE (mechanism parent only)","modes":{"review_confidence":"await targeted TP-vs-FP transaction discriminator","behavioural_unknown_infrastructure":"H0 exact hop-1 WSOL_WRAP_CLOSE 9,999,985,000 lamports","non_match":"all other retained rows"}}}
        OUT.mkdir(parents=True,exist_ok=True)
        report_path=OUT/'p3r_v2_063e_forensic_operation_investigation.v1.json'
        report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        manifest={"schema_version":"P3R_V2_063E_FORENSIC_MANIFEST.v1","report":str(report_path.relative_to(ROOT)),"report_sha256":digest(report_path),"source_digests":{"canonical_membership":digest(MEMBERSHIP)},"frozen_high_waters":{"edges":EDGE_HW,"atomic_flows":ATOMIC_HW},"provider_calls":0,"deterministic_replay":"Run this script against the same DB high-waters; generated_at_utc is the sole non-semantic field."}
        manifest_path=OUT/'p3r_v2_063e_forensic_operation_investigation_manifest.v1.json'
        manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        if args.update_workflow:
            status="RPC_REQUIRED" if verdict.endswith('TARGETED_RPC') else "PROVISIONAL"
            next_action = "Register only after separate approval; use recurrent-funder hybrid for review-only matches." if status == "PROVISIONAL" else "Run the frozen TP-vs-FP targeted RPC discriminator study."
            rpc_requirement = "NOT_REQUIRED_FOR_PROVISIONAL" if status == "PROVISIONAL" else "REQUIRED"
            conn.execute("UPDATE potential_operation_workflows SET workflow_status=?, latest_verdict=?, principal_gap=?, next_action=?, rpc_requirement=?, last_investigated_at=?, updated_at=? WHERE candidate_id=?",(status,verdict,report['rpc_plan']['specific_gap'],next_action,rpc_requirement,int(time.time()),int(time.time()),TARGET)); conn.commit()
        print(json.dumps({"report":str(report_path),"manifest":str(manifest_path),"verdict":verdict,"h0":detector['H0_behaviour_only'],"h1":detector['H1_behaviour_recurrent_funder']},sort_keys=True))
    finally: conn.close()

if __name__ == '__main__': main()
