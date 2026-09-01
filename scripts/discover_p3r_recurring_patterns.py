#!/usr/bin/env python3
"""Deterministic, local-only P3R behavioural recurrence discovery."""
import argparse, hashlib, json, os, sqlite3
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED = {
    "corpus": "38632f80231e29bfe686360898329331f88cf593e7dbac09c4f08a1aa58da651",
    "manifest": "d65fd0f2b248d75fbff0aae82e8f975c390f973dfa66bfd1ac30a635bf85c287",
    "behavioural_discovery": "154790a19f4cbe4d2bb45eb48c9232934042b42060d2ff6cf51e79a07cab829d",
    "structural_evaluation": "8c5d84c26d8356f23ef28aa8b35702f96faa3414b401714a684c3e440d84e28a",
}
VERSION = "p3r-recurring-discovery-v1-exact-raw"

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

def write_new(path, value):
    path = Path(path)
    if path.exists():
        raise RuntimeError("refusing to overwrite immutable artifact: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(canonical(value) + "\n")
        f.flush(); os.fsync(f.fileno())

def prevalence(counter, key, denominator):
    return {"count": counter[key], "rate": counter[key] / denominator if denominator else 0.0}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", required=True); p.add_argument("--manifest", required=True)
    p.add_argument("--behavioural-discovery", required=True); p.add_argument("--structural-evaluation", required=True)
    p.add_argument("--output", required=True); p.add_argument("--membership", required=True); p.add_argument("--signals", required=True)
    p.add_argument("--watchtower-db", default="database/wt_ops_v2.db")
    args = p.parse_args()
    for name, path in (("corpus", args.corpus), ("manifest", args.manifest), ("behavioural_discovery", args.behavioural_discovery), ("structural_evaluation", args.structural_evaluation)):
        if sha(path) != EXPECTED[name]: raise RuntimeError("upstream digest mismatch: " + name)
    code_digest = sha(__file__)
    manifest = json.load(open(args.manifest))
    records = [json.loads(line) for line in open(args.corpus, encoding="ascii") if line.strip()]
    if len(records) != 28883 or len({r["mint"] for r in records}) != 28883: raise RuntimeError("corpus population integrity failure")
    observed = []
    topology = Counter(); amounts = Counter(); mechanisms = Counter(); full = Counter()
    by_full = defaultdict(list); creator_reuse = Counter(); funder_reuse = Counter(); parent_reuse = Counter()
    contradiction_count = 0
    for r in records:
        for key, counter in (("creator", creator_reuse), ("direct_funder", funder_reuse)):
            if r.get(key): counter[r[key]] += 1
        for parent in r.get("parents") or []: parent_reuse[parent] += 1
        obs = r.get("selected_edge_observations") or []
        if (r.get("max_hop_depth") or 0) > (r.get("edge_count") or 0): contradiction_count += 1
        if not obs: continue
        topo = (r.get("edge_count"), r.get("max_hop_depth"), len(r.get("parents") or []), tuple(x.get("mechanism") for x in obs))
        amount = tuple(x.get("amount_lamports") for x in obs)
        mech = tuple(x.get("mechanism") for x in obs)
        sig = (topo, amount, mech)
        topology[topo] += 1; amounts[amount] += 1; mechanisms[mech] += 1; full[sig] += 1
        by_full[sig].append(r); observed.append(r)
    # Candidate rule: exact multi-dimensional signature, at least 4 mints and independent wallet rotation.
    candidates = []
    for sig, members in sorted(by_full.items(), key=lambda kv: canonical(kv[0])):
        if len(members) < 4: continue
        creators = sorted({x.get("creator") for x in members if x.get("creator")})
        funders = sorted({x.get("direct_funder") for x in members if x.get("direct_funder")})
        parents = sorted({v for x in members for v in (x.get("parents") or [])})
        if len(creators) < 3 or len(funders) < 3: continue
        mints = sorted(x["mint"] for x in members)
        cid = "p3r-candidate-" + hashlib.sha256(canonical([VERSION, sig, mints]).encode()).hexdigest()[:16]
        n = len(members)
        strength = "STRONG" if n >= 10 and len(creators) >= 5 and len(funders) >= 5 else "MODERATE" if n >= 5 else "WEAK"
        candidates.append({"candidate_id": cid, "classification": "BEHAVIOURAL_RECURRENCE_PATTERN", "strength": strength,
          "membership_rule": "exact raw selected-edge topology + lamport vector + mechanism vector; n>=4; >=3 distinct creators; >=3 distinct direct funders",
          "mints": mints, "launch_count": n, "unique_creators": len(creators), "unique_direct_funders": len(funders), "unique_parents": len(parents),
          "structural_fingerprint": list(sig[0][:3]), "amount_fingerprint_lamports": list(sig[1]), "mechanism_fingerprint": list(sig[2]),
          "background_prevalence": {"topology": prevalence(topology, sig[0], len(observed)), "amount_vector": prevalence(amounts, sig[1], len(observed)), "mechanism_vector": prevalence(mechanisms, sig[2], len(observed)), "full_signature": prevalence(full, sig, len(observed))},
          "behavioural_coverage": 1.0, "missingness": "none for signature features; other corpus features may be absent", "address_dependence": "address-independent under candidate rule", "supporting_evidence_count": n, "contradictory_evidence_count": sum(1 for x in members if (x.get("max_hop_depth") or 0) > (x.get("edge_count") or 0)),
          "known_operation_overlap": {"watchtower": "unmeasured_reference_mapping", "three_sw2": "unmeasured_reference_mapping"}, "interesting_reason": "exact raw multi-dimensional recurrence persists across rotated creator and direct-funder addresses"})
    # Address-only groups are deliberately not candidates.
    address_groups = {"creator_groups_ge_4": sum(v >= 4 for v in creator_reuse.values()), "direct_funder_groups_ge_4": sum(v >= 4 for v in funder_reuse.values()), "parent_groups_ge_4": sum(v >= 4 for v in parent_reuse.values())}
    coverage = {"population": len(records), "complete_selected_edge_signature": len(observed), "complete_selected_edge_signature_rate": len(observed)/len(records),
      "atomic_wsol_instruction_sequence": sum(bool(r.get("atomic_wsol_instruction_sequence")) for r in records),
      "creator_recurrence_count": sum(r.get("creator_recurrence_count") is not None for r in records), "direct_funder_recurrence_count": sum(r.get("direct_funder_recurrence_count") is not None for r in records), "parent_recurrence_count": sum(r.get("parent_recurrence_count") is not None for r in records)}
    signal_inventory = {"schema_version": VERSION, "bindings": {"corpus_sha256": EXPECTED["corpus"], "corpus_manifest_sha256": EXPECTED["manifest"], "analysis_code_sha256": code_digest}, "signals": [
      {"name":"exact_raw_multi_edge_amount_vector", "definition":"ordered raw lamport vector from selected edge observations; no rounding or bands", "coverage":len(observed), "prevalence":"measured per vector against complete signatures", "address_dependence":"none", "evidence_quality":"qualified selected-edge observations", "later_live_shadow_suitability":"conditional: only where equivalent qualified observations exist"},
      {"name":"combined_topology_amount_mechanism_signature", "definition":"edge count, hop depth, parent count, ordered raw amount vector and mechanism sequence", "coverage":len(observed), "prevalence":"measured exact against complete signatures", "address_dependence":"none", "evidence_quality":"partial local behavioural evidence", "later_live_shadow_suitability":"conditional and requires comparable evidence coverage"},
      {"name":"atomic_wsol_instruction_sequence", "definition":"qualified atomic WSOL sequence", "coverage":coverage["atomic_wsol_instruction_sequence"], "prevalence":"not clustered here because instruction payload semantics are not normalized", "address_dependence":"none", "evidence_quality":"partial local", "later_live_shadow_suitability":"not yet qualified"},
      {"name":"address_recurrence", "definition":"creator/direct-funder/parent repetition", "coverage":len(records), "prevalence":address_groups, "address_dependence":"yes", "evidence_quality":"qualified recurrence counts", "later_live_shadow_suitability":"context only, not standalone discovery proof"}]}
    membership = {"schema_version": VERSION, "bindings": {"corpus_sha256": EXPECTED["corpus"], "analysis_code_sha256": code_digest}, "candidates": candidates}
    verdict = "P3R_NEW_OPERATION_CANDIDATES_FOUND" if candidates else "P3R_DISCOVERY_SIGNALS_FOUND_NO_QUALIFIED_CANDIDATES"
    result = {"schema_version": VERSION, "principal_verdict": verdict, "bindings": {"behavioural_corpus_sha256":EXPECTED["corpus"], "behavioural_manifest_sha256":EXPECTED["manifest"], "behavioural_discovery_sha256":EXPECTED["behavioural_discovery"], "structural_evaluation_sha256":EXPECTED["structural_evaluation"], "analysis_code_sha256":code_digest, "frozen_queue_sha256":manifest.get("frozen_queue_sha256")}, "method": {"deterministic_ordering":"canonical JSON sorting; exact raw lamports; no amount bands or rounding", "candidate_rule":"exact full signature plus wallet rotation thresholds", "address_reuse":"reported only; never qualifies a candidate alone", "scope":"bounded corpus-only descriptive analysis"}, "corpus_integrity":{"records":len(records),"unique_mints":len({r['mint'] for r in records}),"duplicate_mints":0}, "coverage":coverage, "background_controls":{"complete_signature_population":len(observed),"unique_topologies":len(topology),"unique_amount_vectors":len(amounts),"unique_mechanism_vectors":len(mechanisms),"unique_full_signatures":len(full)}, "address_reuse_inventory":address_groups, "candidate_counts":{"total":len(candidates),"WEAK":sum(x['strength']=='WEAK' for x in candidates),"MODERATE":sum(x['strength']=='MODERATE' for x in candidates),"STRONG":sum(x['strength']=='STRONG' for x in candidates)}, "candidates":candidates, "address_removal_persistence":{"qualified_candidates_persisting_without_address_equality":len(candidates),"rule_requires_creator_and_funder_rotation":True}, "known_operation_reference":{"watchtower":"not joined: local reference mapping was not bound into this immutable analysis", "three_sw2":"not joined: no safely bound local mint reference mapping"}, "limitations":["selected-edge evidence covers only a partial local subset", "no launch-relative timing was inferred", "atomic WSOL payloads were not normalized into cluster features", "no causal or identity inference"], "contradiction_inventory":{"max_hop_depth_exceeds_edge_count":contradiction_count}, "safeguards":{"provider_network_calls":False,"source_table_writes":False,"canonical_operation_promotion":False,"identity_attribution":False,"trading_ranking_prediction":False}}
    write_new(args.membership, membership); write_new(args.signals, signal_inventory); write_new(args.output, result)
    print(canonical({"verdict":verdict,"output_sha256":sha(args.output),"membership_sha256":sha(args.membership),"signals_sha256":sha(args.signals),"candidate_count":len(candidates)}))

if __name__ == "__main__": main()
