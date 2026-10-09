#!/usr/bin/env python3
"""Explicit bounded DEV-014 historical-session operator; defaults to dry-run."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.ops.watchtower_historical_backfill_controller import HistoricalBackfillController, SessionBounds
from src.ops.watchtower_historical_execution_binding import HistoricalExecutionBinding

RECON=ROOT/'docs/audits/dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json'
POP=ROOT/'docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json'
MAX=1_000_000

def _load(p:Path)->dict[str,Any]: return json.loads(p.read_text())
def _safe(path:Path, *, protected:tuple[Path,...])->Path:
    if path.is_symlink(): raise SystemExit('SYMLINK_PATH_DENIED')
    resolved=path.resolve(strict=False)
    if any(resolved==root or root in resolved.parents for root in protected): raise SystemExit('PROTECTED_PATH_DENIED')
    return resolved
def _parse_ranks(raw:str)->set[int]:
    ranks={int(x) for x in raw.split(',') if x}
    allowed={22,23,24,26,41,43,46,47,48,50}
    if not ranks or not ranks <= allowed: raise SystemExit('FROZEN_SUBSET_INVALID')
    return ranks
def plan(args:argparse.Namespace)->dict[str,Any]:
    recon,pop=_load(RECON),_load(POP); protected=(ROOT/'database',ROOT/'.dev_runtime')
    state_dir=_safe(args.state_dir,protected=protected); evidence_dir=_safe(args.evidence_dir,protected=protected)
    if state_dir==evidence_dir: raise SystemExit('STATE_EVIDENCE_PATH_MUST_DIFFER')
    bounds=SessionBounds(args.max_requests,args.max_runtime_seconds,args.max_evidence_bytes,args.max_consecutive_failures,args.max_health_failures); bounds.validate()
    ranks=_parse_ranks(args.ranks)
    if args.max_requests>10 or len(ranks)>10: raise SystemExit('REQUEST_CAP_EXCEEDED')
    controller=HistoricalBackfillController(state_dir/'controller.json',recon,pop)
    selected=[x for x in controller.work() if x['rank'] in ranks]
    if [x['rank'] for x in selected] != [22,23,24,26,41,43,46,47,48,50]: raise SystemExit('FROZEN_ORDER_INVALID')
    if any(x['request'] is None for x in selected): raise SystemExit('INELIGIBLE_SUBSET_MEMBER')
    return {'mode':args.mode,'state_path':str(state_dir/'controller.json'),'binding_path':str(evidence_dir/'journal.json'),'evidence_dir':str(evidence_dir),'selected_ranks':[x['rank'] for x in selected],'request_identities':[x['request']['request_identity'] for x in selected],'max_requests':args.max_requests,'max_runtime_seconds':args.max_runtime_seconds,'max_evidence_bytes':args.max_evidence_bytes,'components':{'controller':'HistoricalBackfillController','binding':'HistoricalExecutionBinding','runtime_gate':'Batch4RuntimeGate','budget':'HistoricalForensicsBudgetAdmission','transport':'BirdeyeProductionBinding'},'scheduler':False}
def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=('dry-run','execute'),default='dry-run'); p.add_argument('--ranks',required=True); p.add_argument('--state-dir',type=Path,required=True); p.add_argument('--evidence-dir',type=Path,required=True); p.add_argument('--max-requests',type=int,required=True); p.add_argument('--max-runtime-seconds',type=int,required=True); p.add_argument('--max-evidence-bytes',type=int,required=True); p.add_argument('--max-consecutive-failures',type=int,required=True); p.add_argument('--max-health-failures',type=int,required=True); p.add_argument('--live-opt-in',action='store_true'); a=p.parse_args()
    if a.mode=='execute':
        if not a.live_opt_in: raise SystemExit('EXPLICIT_LIVE_OPT_IN_REQUIRED')
        raise SystemExit('LIVE_EXECUTION_REQUIRES_SEPARATE_RUNTIME_AUTHORIZATION')
    print(json.dumps(plan(a),sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
