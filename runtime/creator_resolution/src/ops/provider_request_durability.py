"""Crash-safe compact provider-request manifest and state transitions."""
from __future__ import annotations
import json, os
from hashlib import sha256
from pathlib import Path

CONTRACT_VERSION="PROVIDER_REQUEST_DURABILITY_CONTRACT_V1"
STATE_MACHINE_VERSION="PROVIDER_REQUEST_STATE_MACHINE_V1"
PLANNED="PLANNED"; DISPATCHING="DISPATCHING"; DISPATCHED="DISPATCHED"; SUCCEEDED="SUCCEEDED"; FAILED="FAILED"; RATE_LIMIT_RETRY_PENDING="RATE_LIMIT_RETRY_PENDING"; RATE_LIMIT_TERMINAL="RATE_LIMIT_TERMINAL"; OUTCOME_UNRETAINED="OUTCOME_UNRETAINED"; CANCELLED="CANCELLED"

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'))
def request_id(spec): return sha256(canonical(spec).encode()).hexdigest()
def durable_write(path: Path, value: dict):
    temp=path.with_suffix(path.suffix+'.tmp'); temp.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    with temp.open('r+') as handle: handle.flush(); os.fsync(handle.fileno())
    os.replace(temp,path)
def plan(*,run_id,requests,planned_at):
    rows=[]
    for request in requests:
        rows.append({'run_id':run_id,'request_identity_hash':request_id(request),'provider':request['provider'],'method_endpoint':request['method_endpoint'],'semantic_family':request['semantic_family'],'token':request['token'],'parameters_digest':sha256(canonical(request['parameters']).encode()).hexdigest(),'planned_at':planned_at,'state':PLANNED,'attempt_number':0})
    if len({x['request_identity_hash'] for x in rows})!=len(rows): raise ValueError('DUPLICATE_LOGICAL_IDENTITY')
    return {'contract_version':CONTRACT_VERSION,'state_machine_version':STATE_MACHINE_VERSION,'run_id':run_id,'requests':rows}
def transition(manifest,index,state,**metadata):
    row=manifest['requests'][index]
    allowed={PLANNED:{DISPATCHING,CANCELLED},DISPATCHING:{DISPATCHED,OUTCOME_UNRETAINED},DISPATCHED:{SUCCEEDED,FAILED,RATE_LIMIT_RETRY_PENDING,RATE_LIMIT_TERMINAL,OUTCOME_UNRETAINED},RATE_LIMIT_RETRY_PENDING:{DISPATCHING,RATE_LIMIT_TERMINAL}}
    if state not in allowed.get(row['state'],set()): raise ValueError('INVALID_STATE_TRANSITION')
    row.update(metadata,state=state); return manifest
def crash_recovery_state(row):
    return OUTCOME_UNRETAINED if row['state'] in {DISPATCHING,DISPATCHED} else row['state']

def execute_durable_provider_request(*, manifest_path: Path, manifest: dict, index: int, provider_call, compact, retry_policy=None, sleep=None, is_run_active=None, persist_compact_result=None, persist_terminal_result=None):
    """Execute one already-planned identity; never dispatch before durable state."""
    active=is_run_active or (lambda: True)
    if not active(): return {'state':'RUN_ABORTED'}
    row=manifest['requests'][index]
    if row['state'] != PLANNED: raise ValueError('LOGICAL_IDENTITY_NOT_DISPATCHABLE')
    durable_write(manifest_path, transition(manifest,index,DISPATCHING,dispatch_started_at=1))
    attempts=1
    response=provider_call()
    status,payload,headers=(response.status_code,response.payload,response.response_headers) if hasattr(response,'status_code') else (*response,{})
    durable_write(manifest_path, transition(manifest,index,DISPATCHED,dispatched_at=2,http_status=status,attempt_number=attempts))
    policy=retry_policy or {}
    delays=tuple(policy.get('delays',()))
    while status==429 and attempts < int(policy.get('max_attempts',1)):
        if not active(): return {'state':'RUN_ABORTED'}
        configured=delays[min(attempts-1,len(delays)-1)] if delays else 0
        try: retry_after=max(0,min(60,int(headers.get('Retry-After')))) if headers.get('Retry-After') is not None else 0
        except (TypeError,ValueError): retry_after=0
        delay=max(configured,retry_after)
        durable_write(manifest_path,transition(manifest,index,RATE_LIMIT_RETRY_PENDING,retry_delay_seconds=delay))
        (sleep or (lambda _:None))(delay)
        if not active(): return {'state':'RUN_ABORTED'}
        attempts+=1
        if not active(): return {'state':'RUN_ABORTED'}
        durable_write(manifest_path,transition(manifest,index,DISPATCHING,dispatch_started_at=attempts,attempt_number=attempts))
        response=provider_call()
        status,payload,headers=(response.status_code,response.payload,response.response_headers) if hasattr(response,'status_code') else (*response,{})
        durable_write(manifest_path,transition(manifest,index,DISPATCHED,dispatched_at=attempts,http_status=status,attempt_number=attempts))
    if status==429 and attempts>=int(policy.get('max_attempts',1)):
        if persist_terminal_result: persist_terminal_result(row,{'state':'RATE_LIMIT_TERMINAL'})
        durable_write(manifest_path,transition(manifest,index,RATE_LIMIT_TERMINAL,outcome_retained_at=attempts,outcome_state='RATE_LIMIT_TERMINAL'))
        return {'state':'RATE_LIMIT_TERMINAL'}
    if 400 <= status < 500:
        if persist_terminal_result: persist_terminal_result(row,{'state':'PROVIDER_BLOCKED','http_status':status})
        durable_write(manifest_path,transition(manifest,index,FAILED,outcome_retained_at=attempts,outcome_state='PROVIDER_BLOCKED',http_status=status))
        return {'state':'PROVIDER_BLOCKED','http_status':status}
    outcome=compact(payload)
    state=SUCCEEDED if outcome.get('state')=='QUALIFIED' else FAILED
    if persist_compact_result:
        try: persist_compact_result(row,outcome)
        except Exception:
            durable_write(manifest_path, transition(manifest,index,FAILED,outcome_retained_at=3,outcome_state='OUTCOME_UNRETAINED'))
            return {'state':'OUTCOME_UNRETAINED'}
    durable_write(manifest_path, transition(manifest,index,state,outcome_retained_at=3,outcome_state=outcome.get('state')))
    return outcome
