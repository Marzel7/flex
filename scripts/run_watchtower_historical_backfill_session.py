#!/usr/bin/env python3
"""Explicit bounded DEV-014 historical-session operator; defaults to dry-run."""
from __future__ import annotations
import argparse, json, os, sys
import time
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.ops.watchtower_historical_backfill_controller import HistoricalBackfillController, SessionBounds
from src.ops.watchtower_historical_execution_binding import HistoricalExecutionBinding
from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission
from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, RuntimeAuthority, resolve_runtime_authority
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, ProviderTransportOutcome

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

def _execute_fixture(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    """Provider-free end-to-end seam; never reachable without the test-only flag."""
    controller = HistoricalBackfillController(manifest['state_path'], _load(RECON), _load(POP))
    binding = HistoricalExecutionBinding(manifest['binding_path'])
    selected = [item for item in controller.work() if item['rank'] in set(manifest['selected_ranks'])]
    root = Path(manifest['state_path']).parent
    root.mkdir(parents=True, exist_ok=True)
    config, db, listener, funding, resolution = (root/'supervisord.conf', root/'canonical.db', root/'listener.log', root/'funding.log', root/'resolution.log')
    config.write_text('[fixture]'); db.touch()
    checkpoint = '{"status":"ok","busy":0,"checkpointed_frames":1,"remaining_frames":0}\n'
    listener.write_text('[WAL_CHECKPOINT] '+checkpoint); funding.touch(); resolution.touch()
    authority = RuntimeAuthority(config, 'http://fixture/healthz', db, listener, (funding, resolution))
    processes = lambda: [
        f'1 0 Sat Oct 10 00:00:00 2026 /usr/local/bin/supervisord -c {config}',
        '2 1 Sat Oct 10 00:00:00 2026 /usr/bin/python3 -m src.core.creator_funding_worker',
        '3 1 Sat Oct 10 00:00:00 2026 /usr/bin/python3 -m src.core.creator_resolution_worker']
    health = lambda _url: (200, {'healthy':True,'db':'ok','wal_warn':False,'workers':{'creator-funding':{'stale':False,'age_s':1},'creator-resolution':{'stale':False,'age_s':1}}})
    class Disk: free=5*1024*1024*1024
    gate = Batch4RuntimeGate(authority, health_fetch=health, process_lines=processes, disk_usage=lambda _: Disk(), wal_size=lambda _: 0)
    calls: list[dict[str, Any]] = []
    def fake_http(request: Any, *, timeout_seconds: int) -> ProviderTransportOutcome:
        calls.append({'url':request.full_url,'timeout':timeout_seconds})
        return ProviderTransportOutcome(200, {'success': True, 'data': {'items': [{'unixTime': selected[0]['request']['params']['time_from'], 'o': 10, 'h': 11, 'l': 9, 'c': 10}]}}, {})
    transport = BirdeyeProductionBinding(api_key='fixture-only-not-a-credential', transport=fake_http)
    # The fixture uses the actual shared adapter against an isolated ledger;
    # production wiring uses the same adapter but remains separately authorized.
    admission = HistoricalForensicsBudgetAdmission(Path(manifest['state_path']).parent / 'fixture-queue')
    with controller:
        result = binding.execute(selected[0], health_gate=gate.check, admit=admission.admit, transport=transport)
    return {'status':'FIXTURE_COMPLETED','request_count':len(calls),'completed_identity':result['request_identity'],
            'live_contention_limitation':'shared budget compliance does not guarantee zero contention with direct LIVE callers'}

def _execute_production(args: argparse.Namespace, manifest: dict[str, Any], *,
                        gate_factory=Batch4RuntimeGate, admission_factory=HistoricalForensicsBudgetAdmission,
                        transport_factory=BirdeyeProductionBinding, authority_resolver=resolve_runtime_authority,
                        crash_at: str | None = None) -> dict[str, Any]:
    """One explicit bounded session; this function never schedules or retries."""
    if not args.supervisor_config or not args.queue_root:
        raise SystemExit('SUPERVISOR_CONFIG_AND_QUEUE_ROOT_REQUIRED')
    queue_root = _safe(args.queue_root, protected=(ROOT/'database', ROOT/'.dev_runtime'))
    controller = HistoricalBackfillController(manifest['state_path'], _load(RECON), _load(POP))
    binding = HistoricalExecutionBinding(manifest['binding_path'])
    authority = authority_resolver(args.supervisor_config)
    gate = gate_factory(authority)
    admission = admission_factory(queue_root)
    provider: Any | None = None
    def transport(request: dict[str, Any]) -> Any:
        # Constructor resolution of BIRDEYE is deliberately deferred until the
        # journal has recorded ATTEMPTED following gate and budget admission.
        nonlocal provider
        if provider is None: provider = transport_factory()
        return provider(request)
    selected = [item for item in controller.work() if item['rank'] in set(manifest['selected_ranks'])]
    started, completed = time.monotonic(), []
    with controller:
        binding.recover()
        for item in selected[:args.max_requests]:
            if time.monotonic()-started >= args.max_runtime_seconds: break
            completed.append(binding.execute(item, health_gate=gate.check, admit=admission.admit, transport=transport, crash_at=crash_at)['request_identity'])
    return {'status':'COMPLETED','request_count':len(completed),'completed_identities':completed,
            'live_contention_limitation':'shared budget compliance does not guarantee zero contention with direct LIVE callers'}
def _execute_production_fixture(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    """Fixed, local-only subprocess fixture for the production composition."""
    root=Path(manifest['state_path']).parent; root.mkdir(parents=True,exist_ok=True)
    config,db,listener,funding,resolution=(root/'fixture.conf',root/'fixture.db',root/'listener.log',root/'funding.log',root/'resolution.log')
    config.write_text('[fixture]'); db.touch(); listener.write_text('[WAL_CHECKPOINT] {"status":"ok","busy":0,"checkpointed_frames":1,"remaining_frames":0}\n'); funding.touch(); resolution.touch()
    authority=RuntimeAuthority(config,'http://fixture/healthz',db,listener,(funding,resolution))
    lines=lambda:[f'1 0 Sat Oct 10 00:00:00 2026 /usr/local/bin/supervisord -c {config}','2 1 Sat Oct 10 00:00:00 2026 /usr/bin/python3 -m src.core.creator_funding_worker','3 1 Sat Oct 10 00:00:00 2026 /usr/bin/python3 -m src.core.creator_resolution_worker']
    scenario=args.test_scenario
    if scenario not in {'healthy','api-unhealthy','wrong-api','funding-stale','resolution-stale','wal-over-limit','checkpoint-stalled','disk-low','duplicate-supervisor','duplicate-creator','critical-wal','global-budget-denied','mint-budget-denied','provider-backoff','malformed-ledger','unreadable-ledger','transport-failure'}: raise SystemExit('TEST_SCENARIO_INVALID')
    payload={'healthy':True,'db':'ok','wal_warn':False,'workers':{'creator-funding':{'stale':False,'age_s':1},'creator-resolution':{'stale':False,'age_s':1}}}
    if scenario=='api-unhealthy': payload['healthy']=False
    if scenario=='funding-stale': payload['workers']['creator-funding']={'stale':True,'age_s':999}
    if scenario=='resolution-stale': payload['workers']['creator-resolution']={'stale':True,'age_s':999}
    health=lambda _:(200,payload)
    if scenario=='checkpoint-stalled': listener.write_text('[WAL_CHECKPOINT] {"status":"ok","busy":1,"checkpointed_frames":0,"remaining_frames":1}\n')
    class Disk: free=5*1024*1024*1024
    if scenario=='duplicate-supervisor': lines=lambda:lines()+[f'4 0 Sat Oct 10 00:00:00 2026 /usr/local/bin/supervisord -c {config}']
    gate=lambda _:Batch4RuntimeGate(authority,health_fetch=health,process_lines=lines,disk_usage=lambda _:Disk(),wal_size=lambda _:600*1024*1024 if scenario=='wal-over-limit' else 0)
    calls=[]
    class Transport:
        def __call__(self, request):
            calls.append(request)
            if scenario=='transport-failure': raise RuntimeError('FIXTURE_TRANSPORT_FAILURE')
            return ProviderTransportOutcome(200,{'success':True,'data':{'items':[{'unixTime':request['request_parameters']['time_from'],'o':10,'h':11,'l':9,'c':10}]}},{})
    result=_execute_production(args,manifest,gate_factory=gate,transport_factory=Transport,authority_resolver=lambda _:authority,crash_at=args.test_crash_at)
    result.update({'fixture_transport_calls':len(calls),'credential_reads':0}); return result
def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=('dry-run','execute'),default='dry-run'); p.add_argument('--ranks',required=True); p.add_argument('--state-dir',type=Path,required=True); p.add_argument('--evidence-dir',type=Path,required=True); p.add_argument('--max-requests',type=int,required=True); p.add_argument('--max-runtime-seconds',type=int,required=True); p.add_argument('--max-evidence-bytes',type=int,required=True); p.add_argument('--max-consecutive-failures',type=int,required=True); p.add_argument('--max-health-failures',type=int,required=True); p.add_argument('--live-opt-in',action='store_true'); p.add_argument('--fixture-fake-live',action='store_true'); p.add_argument('--test-production-fixture',action='store_true'); p.add_argument('--test-scenario',default='healthy'); p.add_argument('--test-crash-at',choices=('PENDING','AFTER_BUDGET','ADMITTED','ATTEMPTED','RESPONSE','EVIDENCE')); p.add_argument('--supervisor-config',type=Path); p.add_argument('--queue-root',type=Path); a=p.parse_args()
    manifest=plan(a)
    if a.fixture_fake_live:
        if a.mode!='execute': raise SystemExit('FIXTURE_REQUIRES_EXECUTE_MODE')
        print(json.dumps(_execute_fixture(a,manifest),sort_keys=True)); return 0
    if a.test_production_fixture:
        if a.mode!='execute' or not a.live_opt_in: raise SystemExit('TEST_FIXTURE_REQUIRES_EXPLICIT_EXECUTE_OPT_IN')
        print(json.dumps(_execute_production_fixture(a,manifest),sort_keys=True)); return 0
    if a.mode=='execute':
        if not a.live_opt_in: raise SystemExit('EXPLICIT_LIVE_OPT_IN_REQUIRED')
        print(json.dumps(_execute_production(a,manifest),sort_keys=True)); return 0
    print(json.dumps(manifest,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
