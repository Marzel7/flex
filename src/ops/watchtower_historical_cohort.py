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
            if not storage_ok(): return {'status':'STORAGE_HOLD','completed':completed,'sessions':sessions}
            execute(record); admitted.append(clock()); completed.append(record['request_identity']); batch.append(record['request_identity'])
        sessions.append(batch)
    return {'status':'EXHAUSTED','completed':completed,'sessions':sessions}
