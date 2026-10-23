"""Provider-free feasibility diagnostics; NOT a runtime pagination owner."""
import ast, hashlib, json, resource, socket, tempfile
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
out=Path(tempfile.mkdtemp(prefix='dev004-bounded-gap-',dir='/private/tmp'))
root=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex')
old=(root/'docs/dev/lightweight_dev004_first_v2/gap_recovery_and_listener_reconciliation/qualify_gap_contract.py').read_text()
assert hashlib.sha256(Path('/Users/kevinkeaveney/Production/flex-listener/src/core/pumpfun_curve_listener.py').read_bytes()).hexdigest()=='751d40d14150b112513cfbed44329cf1c9f0e3e58f34369dd8f29550a1cd3a5f'
old=old.replace("out=Path('/private/tmp/dev004-listener-reconcile-q0rho3wv')",'out=Path('+repr(str(out))+')')
exec(compile(old,'retained_actual_loop_counterexample','exec'))
# Protocol feasibility only. Signatures below are synthetic test identities,
# not valid chain signatures or canonical-persistence evidence.
expected=[f'synthetic-sig-{i:03}' for i in range(150,0,-1)]
anchor='synthetic-pre-gap-anchor'
population=expected+[anchor]
requests=[]; recovered=[]; cursor=None; reached=False
for step in range(3):
    start=0 if cursor is None else population.index(cursor)+1
    page=population[start:start+100]
    requests.append({'limit':100,'before':cursor,'returned':len(page)})
    if not page: break
    for identity in page:
        if identity==anchor: reached=True; break
        recovered.append(identity)
    if reached: break
    cursor=page[-1]
# Without an independent completeness contract, omission inside a successful
# page produces the same transcript as a genuinely smaller source population.
transcript=[population[:100],population[100:]]
world_a=population
world_b=population[:75]+['synthetic-hidden-event']+population[75:]
returned_a=transcript
returned_b=[[s for s in p if s!='synthetic-hidden-event'] for p in transcript]
checks={
 'bounded_before_model_recovers_exact_150':recovered==expected and reached and len(requests)==2,
 'mock_pages_are_100_and_51_including_anchor':[x['returned'] for x in requests]==[100,51],
 'lower_anchor_does_not_detect_silent_omission':returned_a==returned_b and len(world_b)==len(world_a)+1,
}
assert all(checks.values())
result={'status':'HOLD_PROVIDER_COVERAGE_AND_DURABLE_CONTINUATION_NOT_ESTABLISHED','diagnostics':checks,
 'model_only':True,'runtime_pagination_implemented':False,
 'model_requests':requests,'model_recovered_identities':recovered,
 'actual_loop_result':json.loads((out/'gap-qualification.json').read_text()),
 'canonical_persistence_verified':False,
 'restart_failure_matrix':'NOT_RUN_STOP_RULE: no accepted durable gap continuation/completion owner; a newly invented test owner would not qualify existing runtime',
 'coverage_limitation':'Successful result pages with a before cursor and lower anchor do not independently prove absence of silently omitted events. Provider archival/completeness authority for the bounded interval has not been established.',
 'provider_calls':0,'production_writes':0,'runtime_deployment_required':False}
(out/'bounded-gap-diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
print('OUTPUT_DIRECTORY='+str(out))
print(json.dumps({'status':result['status'],'checks':checks,'model_recovered':len(recovered),'maximum_file_bytes':max(p.stat().st_size for p in out.iterdir())}))
