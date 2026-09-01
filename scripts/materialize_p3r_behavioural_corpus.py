#!/usr/bin/env python3
"""Materialize the qualified partial P3R behavioural feature contract read-only."""

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


EXPECTED = {"corpus":"a1779e0f78f7aff8813e7ec4402073c7a6c99232fc80f0f8dcdd562a945524ce", "queue":"d111116fd7a1e149e8fea30498cef6c35e3de534cdefef9da78dd4223daff5c3", "manifest":"c5aa554ab03f64bad048815e984be737e165f88982f4da5222d65fdb87836260", "qualification":"b7969bce6af3c2f15a88da9ab612ef165dd3d181e7cac85b01d08c61d78bbe39", "evaluation":"8c5d84c26d8356f23ef28aa8b35702f96faa3414b401714a684c3e440d84e28a", "discovery":"154790a19f4cbe4d2bb45eb48c9232934042b42060d2ff6cf51e79a07cab829d"}


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def identity(path: Path) -> dict:
    s=path.stat(); return {"path":str(path.resolve()),"size_bytes":s.st_size,"mtime_ns":s.st_mtime_ns,"inode":s.st_ino,"access":"sqlite_uri_mode_ro_snapshot"}


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--namespace',type=Path,required=True); p.add_argument('--qualification',type=Path,required=True); p.add_argument('--evaluation',type=Path,required=True); p.add_argument('--discovery',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--manifest-output',type=Path,required=True); args=p.parse_args()
    ns=args.namespace; raw=ns/'p3r_historical_features.jsonl'; queue=ns/'frozen_queue.txt'; checkpoint=ns/'p3r_historical_features.checkpoint.json'; manifest=ns/'p3r_historical_features.clean_rebuild_manifest.json'
    inputs={"corpus":digest(raw),"queue":digest(queue),"qualification":digest(args.qualification),"evaluation":digest(args.evaluation),"discovery":digest(args.discovery)}
    cp=json.loads(checkpoint.read_text()); mf=json.loads(manifest.read_text()); disc=json.loads(args.discovery.read_text())
    if inputs != {k:EXPECTED[k] for k in inputs} or cp.get('run_manifest_digest') != EXPECTED['manifest'] or disc.get('principal_verdict') != 'P3R_BEHAVIOURAL_EVIDENCE_PARTIAL_LOCAL': raise SystemExit('P3R_BEHAVIOURAL_MATERIALIZATION_INPUT_BINDING_FAILURE')
    base=[json.loads(x) for x in raw.read_text().splitlines()]; ordered=queue.read_text().splitlines()
    if len(base)!=28883 or [r['mint'] for r in base]!=ordered or len(set(ordered))!=28883: raise SystemExit('P3R_BEHAVIOURAL_MATERIALIZATION_POPULATION_FAILURE')
    creator_counts=Counter(r['creator'] for r in base if r.get('creator')); funder_counts=Counter(r['direct_funder'] for r in base if r.get('direct_funder')); parent_counts=Counter(x for r in base for x in (r.get('parents') or []))
    source=Path('database/wt_ops_v2.db'); before=identity(source)
    conn=sqlite3.connect(f'file:{source.resolve()}?mode=ro',uri=True); conn.execute('begin')
    selected=defaultdict(list); atomic=defaultdict(list)
    mints=set(ordered)
    for mint,amount,block_time,sig,mechanism,hop,key in conn.execute("select mint,amount_lamports,block_time,signature,mechanism,hop_depth,evidence_key from wt_walkback_edge_candidates where selection_status='SELECTED'"):
        if mint in mints: selected[mint].append({"amount_lamports":amount,"block_time":block_time,"signature":sig,"mechanism":mechanism,"hop_depth":hop,"evidence_key":key})
    for mint,sig,block_time,lamports,sequence,created,sync,closed in conn.execute('select mint,signature,block_time,transfer_lamports,instruction_order_json,has_create,has_sync_native,has_close from wt_walkback_atomic_flows'):
        if mint in mints: atomic[mint].append({"signature":sig,"block_time":block_time,"transfer_lamports":lamports,"instruction_order_json":sequence,"has_create":bool(created),"has_sync_native":bool(sync),"has_close":bool(closed)})
    conn.close(); after=identity(source)
    for values in selected.values(): values.sort(key=lambda x:(x['hop_depth'],x['block_time'],x['signature'],x['evidence_key']))
    for values in atomic.values(): values.sort(key=lambda x:(x['block_time'],x['signature']))
    def make(row):
        mint=row['mint']; edges=selected.get(mint); flows=atomic.get(mint)
        return {"mint":mint,"creator":row['creator'],"direct_funder":row['direct_funder'],"edge_count":row['edge_count'],"max_hop_depth":row['max_hop_depth'],"parents":row['parents'],"mechanisms":row['mechanisms'],"selected_edge_observations":edges or None,"selected_edge_amount_lamports":[x['amount_lamports'] for x in edges] if edges else None,"selected_edge_block_time":[x['block_time'] for x in edges] if edges else None,"selected_edge_signature_and_mechanism":[{"signature":x['signature'],"mechanism":x['mechanism'],"evidence_key":x['evidence_key']} for x in edges] if edges else None,"atomic_wsol_instruction_sequence":flows or None,"creator_recurrence_count":creator_counts.get(row['creator']) if row.get('creator') else None,"direct_funder_recurrence_count":funder_counts.get(row['direct_funder']) if row.get('direct_funder') else None,"parent_recurrence_count":[parent_counts[x] for x in row['parents']] if row.get('parents') else None}
    records=[make(row) for row in base]; lines=[json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in records]
    if lines != [json.dumps(make(row),sort_keys=True,separators=(',',':'))+'\n' for row in base]: raise SystemExit('P3R_BEHAVIOURAL_MATERIALIZATION_NONDETERMINISTIC_REPLAY')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        f.writelines(lines); f.flush(); os.fsync(f.fileno())
    out_digest=digest(args.output); fields=["mint","creator","direct_funder","edge_count","max_hop_depth","parents","mechanisms","selected_edge_observations","selected_edge_amount_lamports","selected_edge_block_time","selected_edge_signature_and_mechanism","atomic_wsol_instruction_sequence","creator_recurrence_count","direct_funder_recurrence_count","parent_recurrence_count"]
    coverage={field:{"eligible_denominator":len(records),"populated_numerator":sum(r.get(field) is not None for r in records),"missing":sum(r.get(field) is None for r in records)} for field in fields}
    for value in coverage.values(): value['coverage_pct']=value['populated_numerator']*100/len(records)
    contract={"version":"p3r-behavioural-feature-contract-v1","fields":fields,"ordering":"frozen mint ASCII order; edge=(hop_depth,block_time,signature,evidence_key); atomic=(block_time,signature)","null_policy":"null is explicit missing evidence and is never imputed","raw_units":{"selected_edge_amount_lamports":"lamports","selected_edge_block_time":"Unix seconds"}}
    out_manifest={"artifact_type":"P3R_BEHAVIOURAL_CORPUS_MANIFEST","run_id":"p3r-behavioural-corpus-"+ns.name,"materialized_at_utc":datetime.now(timezone.utc).isoformat(),"authoritative_bindings":{"frozen_queue_sha256":inputs['queue'],"clean_corpus_sha256":inputs['corpus'],"qualification_sha256":inputs['qualification'],"structural_evaluation_sha256":inputs['evaluation'],"behavioural_discovery_sha256":inputs['discovery'],"run_manifest_digest":EXPECTED['manifest']},"materializer_code":{"path":str(Path(__file__).resolve()),"sha256":digest(Path(__file__).resolve())},"source_snapshot":{"before":before,"after":after,"sqlite_read_transaction":True},"feature_contract":contract,"feature_contract_digest":canonical_digest(contract),"output":{"path":str(args.output),"sha256":out_digest,"records":len(records),"unique_mints":len({r['mint'] for r in records}),"duplicate_mints":len(records)-len({r['mint'] for r in records}),"deterministic_replay":True,"coverage":coverage}}
    args.manifest_output.parent.mkdir(parents=True,exist_ok=True)
    with args.manifest_output.open('x',encoding='utf-8') as f: json.dump(out_manifest,f,sort_keys=True,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    print(json.dumps({"output":str(args.output),"output_sha256":out_digest,"manifest":str(args.manifest_output),"manifest_sha256":digest(args.manifest_output),"records":len(records)})); return 0


if __name__=='__main__': raise SystemExit(main())
