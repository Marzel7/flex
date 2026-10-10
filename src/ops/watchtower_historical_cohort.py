"""Finite, explicitly authorized DEV-014 manifest coordinator; never schedules."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any
from src.ops.watchtower_historical_execution_binding import HistoricalExecutionBinding

MAX_SESSION=50
def digest(value: Any)->str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

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
