"""Finite, explicitly authorized DEV-014 manifest coordinator; never schedules."""
from __future__ import annotations
import hashlib, json, time, subprocess, sys
from pathlib import Path
from typing import Any
from src.ops.watchtower_historical_execution_binding import HistoricalExecutionBinding

MAX_SESSION=50
HISTORICAL_WINDOW=60
HISTORICAL_LIMIT=10
SESSION_COOLDOWN=60
def digest(value: Any)->str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def cohort_authorization(*, chronology_hash: str, identities: list[str], authority_id: str,
                         max_runtime_seconds: int=3600)->dict[str,Any]:
    if not authority_id or len(identities)!=len(set(identities)) or not identities: raise ValueError('EXPLICIT_COHORT_AUTHORIZATION_REQUIRED')
    value={'schema':'dev014.finite-cohort-authorization.v1','population_identity':'WATCHTOWER_FORENSIC_POPULATION_V2_20261009','chronology_hash':chronology_hash,'authority_id':authority_id,'request_identities':identities,'max_total_paid_requests':len(identities),'max_requests_per_session':50,'max_runtime_seconds':max_runtime_seconds,'historical_limit_per_60s':10,'session_cooldown_seconds':60,'max_cohort_bytes':100*1024*1024,'min_free_bytes':4*1024**3,'cancellation':'STOP','safety_hold':'STOP_NO_RESTART'}; value['content_hash']=digest(value); return value

def validate_cohort_authorization(value: dict[str, Any]) -> dict[str, Any]:
    """Validate operator-supplied finite authority; this never creates authority."""
    if not isinstance(value, dict): raise ValueError('EXPLICIT_COHORT_AUTHORIZATION_REQUIRED')
    supplied=value.get('content_hash'); candidate=dict(value); candidate.pop('content_hash',None)
    required={'schema':'dev014.finite-cohort-authorization.v1','population_identity':'WATCHTOWER_FORENSIC_POPULATION_V2_20261009',
              'max_requests_per_session':MAX_SESSION,'historical_limit_per_60s':HISTORICAL_LIMIT,
              'session_cooldown_seconds':SESSION_COOLDOWN,'max_cohort_bytes':100*1024*1024,
              'min_free_bytes':4*1024**3,'cancellation':'STOP','safety_hold':'STOP_NO_RESTART'}
    if supplied != digest(candidate) or any(candidate.get(key)!=expected for key,expected in required.items()):
        raise ValueError('COHORT_AUTHORIZATION_INVALID')
    identities=candidate.get('request_identities')
    if (not isinstance(candidate.get('authority_id'),str) or not candidate['authority_id'] or not isinstance(identities,list)
            or not identities or len(identities)>524 or len(identities)!=len(set(identities))
            or candidate.get('max_total_paid_requests')!=len(identities)):
        raise ValueError('COHORT_AUTHORIZATION_INVALID')
    return value

def reconcile_journals(journals: list[str|Path]) -> dict[str, Any]:
    """Return a conservative no-repeat set from every authoritative paid journal."""
    if len(journals)!=4: raise ValueError('FOUR_AUTHORITATIVE_JOURNALS_REQUIRED')
    blocked_ids:set[str]=set(); blocked_mints:set[str]=set(); outcomes:dict[str,str]={}; sources=[]
    for raw_path in journals:
        path=Path(raw_path)
        if not path.is_file() or path.is_symlink(): raise ValueError('AUTHORITATIVE_JOURNAL_UNAVAILABLE')
        records=HistoricalExecutionBinding(path).read()['records']; sources.append(str(path))
        for record in records:
            identity,mint,state=record.get('request_identity'),record.get('mint'),record.get('state')
            # Every durable intent/attempt is a no-repeat exclusion until a human reconciles it.
            if state in {'ADMISSION_INTENT','ADMITTED','ATTEMPTED','COMPLETED','OUTCOME_UNKNOWN'}:
                if not isinstance(identity,str) or not identity or not isinstance(mint,str) or not mint:
                    raise ValueError('AUTHORITATIVE_JOURNAL_RECORD_INVALID')
                previous=outcomes.get(identity)
                if previous and previous!=state: raise ValueError('AUTHORITATIVE_JOURNAL_IDENTITY_CONFLICT')
                outcomes[identity]=state; blocked_ids.add(identity); blocked_mints.add(mint)
    return {'journals':sources,'blocked_request_identities':blocked_ids,'blocked_mints':blocked_mints,'outcomes':outcomes}

def cli_executor(cli: str|Path, base: list[str], *, runner: Any=subprocess.run):
    """Adapter only: caller provides the existing CLI's explicit fixture/live args."""
    def execute(manifest_path: str|Path)->dict[str,Any]:
        result=runner([sys.executable,str(cli),*base,'--manifest',str(manifest_path)],text=True,capture_output=True,check=False)
        if result.returncode: raise RuntimeError('COHORT_CLI_HOLD:'+result.stderr[-512:])
        return json.loads(result.stdout)
    return execute

def remaining(controller: Any, journal: str|Path)->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    records=HistoricalExecutionBinding(journal).read()['records']; terminal={r.get('request_identity') for r in records if r.get('state') in {'COMPLETED','OUTCOME_UNKNOWN'}}
    eligible=[]; deferred=[]
    for item in sorted(controller.work(),key=lambda x:x['rank']):
        if not item.get('request'): deferred.append(item); continue
        if item['request']['request_identity'] not in terminal: eligible.append(item)
    return eligible,deferred

def manifests(controller: Any,journal: str|Path,authorization: str)->list[dict[str,Any]]:
    if not isinstance(authorization,str) or not authorization: raise ValueError('EXPLICIT_COHORT_AUTHORIZATION_REQUIRED')
    work,_=remaining(controller,journal); out=[]
    for offset in range(0,len(work),MAX_SESSION):
        records=[]
        for item in work[offset:offset+MAX_SESSION]:
            request=item['request']; record={'rank':item['rank'],'mint':item['mint'],'anchor':item['anchor'],'requested_window':{'time_from':request['params']['time_from'],'time_to':request['params']['time_to'],'interval':'1m'},'request_identity':request['request_identity']}
            if 'entry_mc_usd' in item: record['entry_mc_usd']=item['entry_mc_usd']
            records.append(record)
        value={'schema':'dev014.frozen-historical-acquisition-manifest.v1','version':1,'population_identity':'WATCHTOWER_FORENSIC_POPULATION_V2_20261009','authorization':{'kind':'EXPLICIT_FROZEN_MANIFEST','authority_id':authorization,'max_requests':len(records)},'max_authorized_requests':len(records),'records':records}; value['content_hash']=digest(value); out.append(value)
    return out

def authorized_manifests(controller: Any, authorization: dict[str, Any], journals: list[str|Path],
                         session_journal: str|Path|None=None) -> list[dict[str, Any]]:
    """Freeze only authorized, unrepeated work in chronological batches of at most 50."""
    authorization=validate_cohort_authorization(authorization); history=reconcile_journals(journals)
    if session_journal is not None:
        for record in HistoricalExecutionBinding(session_journal).read()['records']:
            identity,mint,state=record.get('request_identity'),record.get('mint'),record.get('state')
            if state in {'ADMISSION_INTENT','ADMITTED','ATTEMPTED','COMPLETED','OUTCOME_UNKNOWN'}:
                if not isinstance(identity,str) or not isinstance(mint,str): raise ValueError('SESSION_JOURNAL_RECORD_INVALID')
                history['outcomes'][identity]=state; history['blocked_request_identities'].add(identity); history['blocked_mints'].add(mint)
    allowed=set(authorization['request_identities']); selected=[]; found=set()
    for item in sorted(controller.work(), key=lambda x:x['rank']):
        request=item.get('request')
        if not request: continue
        identity=request.get('request_identity')
        if identity not in allowed: continue
        if identity in history['blocked_request_identities'] or item['mint'] in history['blocked_mints']: continue
        anchor=request.get('params',{}).get('time_from')
        if (identity in found or not isinstance(anchor,int) or request['params'].get('time_to')!=anchor+3600):
            raise ValueError('COHORT_MEMBERSHIP_INVALID')
        found.add(identity); selected.append(item)
    # A terminal record is a permitted omission; an intent/attempt is an ambiguity
    # and must stop rather than let a later creation-time identity replay it.
    missing=allowed-found
    unresolved={identity for identity in missing if history['outcomes'].get(identity) in {'ADMISSION_INTENT','ADMITTED','ATTEMPTED'}}
    if unresolved: raise ValueError('COHORT_UNRESOLVED_OUTCOME')
    selectable={item['request']['request_identity'] for item in controller.work() if item.get('request')}
    if missing-selectable: raise ValueError('AUTHORIZED_IDENTITY_NOT_SELECTABLE')
    result=[]
    for number,offset in enumerate(range(0,len(selected),MAX_SESSION),start=1):
        records=[]
        for item in selected[offset:offset+MAX_SESSION]:
            request=item['request']; records.append({'rank':item['rank'],'mint':item['mint'],'anchor':item['anchor'],
                'requested_window':{'time_from':request['params']['time_from'],'time_to':request['params']['time_to'],'interval':'1m'},
                'request_identity':request['request_identity'], **({'entry_mc_usd':item['entry_mc_usd']} if 'entry_mc_usd' in item else {})})
        value={'schema':'dev014.frozen-historical-acquisition-manifest.v1','version':1,'population_identity':authorization['population_identity'],
               'authorization':{'kind':'EXPLICIT_FROZEN_MANIFEST','authority_id':authorization['authority_id'],'cohort_content_hash':authorization['content_hash'],'max_requests':len(records)},
               'max_authorized_requests':len(records),'session_number':number,'records':records}
        value['content_hash']=digest(value); result.append(value)
    return result

def _write_manifest(root: str|Path, number: int, manifest: dict[str, Any]) -> Path:
    """Persist one compact frozen manifest; caller supplies an isolated/cohort root."""
    directory=Path(root)
    if not directory.is_dir() or directory.is_symlink(): raise RuntimeError('COHORT_MANIFEST_ROOT_UNAVAILABLE')
    path=directory/f'dev014_session_{number:03d}.manifest.json'
    if path.exists() or path.is_symlink(): raise RuntimeError('COHORT_MANIFEST_PATH_CONFLICT')
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    if len(raw)>=1_000_000: raise RuntimeError('COHORT_MANIFEST_BOUND_EXCEEDED')
    temporary=path.with_suffix('.json.tmp')
    if temporary.exists(): raise RuntimeError('COHORT_MANIFEST_PATH_CONFLICT')
    temporary.write_bytes(raw); temporary.replace(path)
    return path

def _storage_permitted(storage_ok: Any) -> bool:
    """Guards raise on denial; only an explicit False callback means HOLD."""
    try:
        return storage_ok() is not False
    except Exception:
        return False

def execute_authorized_sessions(controller: Any, authorization: dict[str, Any], *, authoritative_journals: list[str|Path],
                                session_journal: str|Path, manifest_root: str|Path, execute_session: Any,
                                health: Any, clock: Any=time.monotonic, sleep: Any=time.sleep,
                                cancelled: Any=lambda:False, storage_ok: Any=lambda:True) -> dict[str, Any]:
    """Finite subprocess-session binding; only a durable terminal journal advances work."""
    validate_cohort_authorization(authorization)
    completed=[]; sessions=[]
    while True:
        if cancelled(): return {'status':'CANCELLED','completed':completed,'sessions':sessions}
        health()
        if not _storage_permitted(storage_ok): return {'status':'STORAGE_HOLD','completed':completed,'sessions':sessions}
        # The session journal is reconciled separately; these four are immutable paid authority.
        frozen=authorized_manifests(controller,authorization,authoritative_journals,session_journal)
        if not frozen: return {'status':'EXHAUSTED','completed':completed,'sessions':sessions}
        manifest=frozen[0]
        path=_write_manifest(manifest_root,len(sessions)+1,manifest)
        if cancelled(): return {'status':'CANCELLED','completed':completed,'sessions':sessions}
        health(); execute_session(path)  # adapter waits for subprocess completion
        paid=reconcile_journals(authoritative_journals)
        outcome={**paid['outcomes']}
        for record in HistoricalExecutionBinding(session_journal).read()['records']:
            identity,state=record.get('request_identity'),record.get('state')
            if isinstance(identity,str) and state in {'COMPLETED','OUTCOME_UNKNOWN','ADMISSION_INTENT','ADMITTED','ATTEMPTED'}:
                outcome[identity]=state
        expected=[record['request_identity'] for record in manifest['records']]
        unresolved=[identity for identity in expected if outcome.get(identity) not in {'COMPLETED','OUTCOME_UNKNOWN'}]
        if unresolved: return {'status':'JOURNAL_HOLD','completed':completed,'sessions':sessions,'unresolved':unresolved}
        completed.extend(expected); sessions.append(expected)
        # Do not mutate/advance a manifest or invoke a provider during cooldown.
        deadline=clock()+SESSION_COOLDOWN
        while clock()<deadline:
            if cancelled(): return {'status':'CANCELLED','completed':completed,'sessions':sessions}
            health(); sleep(min(1,deadline-clock()))

def run_finite(manifest_list: list[dict[str,Any]], *, execute: Any, health: Any,
               clock: Any=time.monotonic, sleep: Any=time.sleep, cancelled: Any=lambda:False,
               storage_ok: Any=lambda:True)->dict[str,Any]:
    """Finite caller-owned execution loop; no scheduler, retries, or authority."""
    completed=[]; admitted=[]; sessions=[]
    for index, manifest in enumerate(manifest_list):
        if index:
            deadline=clock()+SESSION_COOLDOWN
            while clock()<deadline:
                if cancelled(): return {'status':'CANCELLED','completed':completed,'sessions':sessions}
                health()
                sleep(min(1,deadline-clock()))
        batch=[]
        for record in manifest['records']:
            while len([x for x in admitted if x>clock()-HISTORICAL_WINDOW]) >= HISTORICAL_LIMIT:
                if cancelled(): return {'status':'CANCELLED','completed':completed,'sessions':sessions}
                health(); sleep(1)
            if cancelled(): return {'status':'CANCELLED','completed':completed,'sessions':sessions}
            health()
            if not _storage_permitted(storage_ok): return {'status':'STORAGE_HOLD','completed':completed,'sessions':sessions}
            execute(record); admitted.append(clock()); completed.append(record['request_identity']); batch.append(record['request_identity'])
        sessions.append(batch)
    return {'status':'EXHAUSTED','completed':completed,'sessions':sessions}
