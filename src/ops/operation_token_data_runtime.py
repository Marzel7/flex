"""Thin generic production composition for the token-data request DAG."""
from __future__ import annotations
import time
from typing import Mapping, Sequence

from pathlib import Path
from .operation_token_data_runner import REQUEST_DAG_VERSION, plan_token_data_requests, _durability_spec
from .provider_request_durability import durable_write, execute_durable_provider_request, plan
from .token_data_fact_adapters import create_fact, migration_fact, lifecycle_fact, fx_fact, token_fact_result
from .opening_sequence_aggregator import initialize_opening_sequence, ingest_opening_sequence_block, finalize_opening_sequence
from .token_data_run_state import create_run,get_run_state,complete_run,abort_run,DEFAULT_DB
from .token_data_compact_results import persist_compact_request_result

TOKEN_DATA_PRODUCTION_RUNTIME_VERSION = "TOKEN_DATA_PRODUCTION_RUNTIME_V1"

def initialize_operation_token_data_run(*, run_id, run_state_db=DEFAULT_DB):
    """Shared authority gate for full and family-scoped production compositions."""
    create_run(run_id, run_state_db)
    if get_run_state(run_id, run_state_db) != 'ACTIVE': raise RuntimeError('RUN_STATE_REQUIRED')
    return run_id


def run_operation_token_data_research(*, run_id: str, token_manifest: Sequence[Mapping],
                                      requested_fact_families: Sequence[str], authorization_envelope,
                                      retained_evidence: Mapping[str, Mapping], provider_bindings,
                                      provider_request_timestamp: int, dry_run: bool = False,
                                      retry_sleeper=None, run_state_db=DEFAULT_DB):
    """Compose existing generic components; transport is injected by the caller.

    In dry-run mode this returns the constructible durable request nodes without
    invoking any provider binding.  Execution is deliberately delegated to the
    existing generic runner, never to this composition layer.
    """
    try: initialize_operation_token_data_run(run_id=run_id,run_state_db=run_state_db)
    except RuntimeError: return {'state':'RUN_ABORTED','run_id':run_id,'plans':[],'results':[]}
    plans = []; results=[]
    for token in token_manifest:
        facts = dict(retained_evidence.get(token["mint"], {}))
        planned=plan_token_data_requests(
            token=token, requested_fact_families=requested_fact_families,
            retained_facts=facts, authorization_envelope=authorization_envelope,
            provider_request_timestamp=provider_request_timestamp,
        )
        plans.append({"mint": token["mint"], **planned})
        if dry_run: continue
        executed=0; compact={}; opening=None
        while planned['state']=='READY' and planned['requests']:
            if get_run_state(run_id,run_state_db) != 'ACTIVE': return {'state':'RUN_ABORTED','run_id':run_id,'plans':plans,'results':results}
            nodes=planned['requests']; mp=Path('docs/audits')/f'{run_id}.{token["mint"]}.{executed}.manifest.json'
            manifest=plan(run_id=run_id,requests=[_durability_spec(n) for n in nodes],planned_at=provider_request_timestamp);durable_write(mp,manifest)
            for i,node in enumerate(nodes):
                if get_run_state(run_id,run_state_db) != 'ACTIVE': return {'state':'RUN_ABORTED','run_id':run_id,'plans':plans,'results':results}
                family=node['request_family']; binding=provider_bindings[(node['provider'],node['method_endpoint'])]
                def ingest(payload,node=node,family=family):
                    if family=='CHAIN_CREATE_TRANSACTION':
                        value=create_fact(payload,node['request_parameters']['signature'],token['mint']); facts.update({'CREATE_FACT':'FACT_QUALIFIED' if value['status']=='QUALIFIED' else 'FACT_INSUFFICIENT_EVIDENCE','CREATE_SLOT':value.get('slot'),'CREATE_TIMESTAMP':value.get('timestamp'),'FX_TARGET_TIMESTAMP':value.get('timestamp')});compact['create']=value
                    elif family=='CHAIN_MIGRATION_TRANSACTION':
                        value=migration_fact(payload,node['request_parameters']['signature'],token['mint'],token.get('pool')); facts['MIGRATION']='FACT_QUALIFIED' if value['status']=='QUALIFIED' else 'FACT_INSUFFICIENT_EVIDENCE';compact['migration']=value
                    elif family=='PRICE_HISTORY':
                        value=lifecycle_fact(payload); facts['PRICE_HISTORY']='FACT_QUALIFIED' if value['state']=='QUALIFIED' else 'FACT_INSUFFICIENT_EVIDENCE';compact['lifecycle']=value
                    elif family=='SOL_USD_FX':
                        value=fx_fact(payload,node['request_parameters']['time_to']); facts['SOL_USD_FX']='FACT_QUALIFIED' if value['state']=='QUALIFIED' else 'FACT_INSUFFICIENT_EVIDENCE';compact['fx']=value
                    else:
                        nonlocal opening
                        if opening is None: opening=initialize_opening_sequence(mint=token['mint'],create_signature=compact['create']['signature'],create_slot=facts['CREATE_SLOT'],create_timestamp=facts['CREATE_TIMESTAMP'],creator=token.get('creator',''))
                        ingest_opening_sequence_block(opening,slot=node['request_parameters']['slot'],block_payload=payload)
                        facts['OPENING_NEXT_SLOT']=opening.get('next_required_slot')
                        if opening['opening_complete']:
                            value=finalize_opening_sequence(opening);compact['opening']=value;facts['OPENING_STOPPED']=True;facts['OPENING_STATE']='FACT_QUALIFIED' if value['state']=='QUALIFIED' else 'FACT_INSUFFICIENT_EVIDENCE'
                        else: value={'state':'QUALIFIED'}
                    return {'state':'QUALIFIED' if value.get('status',value.get('state'))=='QUALIFIED' else 'INSUFFICIENT_EVIDENCE','compact_result':value}
                def persist(row,outcome,node=node,family=family):
                    qualified=outcome.get('state')=='QUALIFIED'
                    if family=='CHAIN_MIGRATION_TRANSACTION' and qualified and 'first_pumpswap_pool_mc_sol' not in outcome.get('compact_result',{}):
                        raise ValueError('MIGRATION_COMPACT_POOL_MC_REQUIRED')
                    persist_compact_request_result(run_id=run_id,request=row,mint=token['mint'],terminal_state='SUCCEEDED' if qualified else 'FAILED',fact_state='FACT_QUALIFIED' if qualified else 'FACT_INSUFFICIENT_EVIDENCE',canonical_result=outcome.get('compact_result',{}),provider_status=200,parser_version='TOKEN_DATA_FACT_RESULT_V1')
                def persist_terminal(row,outcome,node=node):
                    state=outcome['state']; fact={'PROVIDER_BLOCKED':'FACT_PROVIDER_BLOCKED','RATE_LIMIT_TERMINAL':'FACT_RATE_LIMITED_TERMINAL'}.get(state,'FACT_OUTCOME_UNRETAINED')
                    persist_compact_request_result(run_id=run_id,request=row,mint=token['mint'],terminal_state=state,fact_state=fact,canonical_result={},provider_status=outcome.get('http_status'),parser_version='TOKEN_DATA_FACT_RESULT_V1')
                outcome=execute_durable_provider_request(manifest_path=mp,manifest=manifest,index=i,provider_call=lambda b=binding,n=node:b(n,timeout_seconds=45),compact=ingest,retry_policy={'max_attempts':9,'delays':(2,5,10,30,60,60,60,60)},sleep=retry_sleeper or time.sleep,is_run_active=lambda:get_run_state(run_id,run_state_db)=='ACTIVE',persist_compact_result=persist,persist_terminal_result=persist_terminal)
                if outcome.get('state')=='RUN_ABORTED': return {'state':'RUN_ABORTED','run_id':run_id,'plans':plans,'results':results}
                if outcome.get('state')=='PROVIDER_BLOCKED':
                    facts[{'CHAIN_CREATE_TRANSACTION':'CREATE_FACT','CHAIN_MIGRATION_TRANSACTION':'MIGRATION','PRICE_HISTORY':'PRICE_HISTORY','SOL_USD_FX':'SOL_USD_FX','OPENING_SEQUENCE_BLOCK':'OPENING_STATE'}[family]]='FACT_PROVIDER_BLOCKED'
                executed+=1
            planned=plan_token_data_requests(token=token,requested_fact_families=requested_fact_families,retained_facts=facts,authorization_envelope=authorization_envelope,provider_request_timestamp=provider_request_timestamp,already_planned=executed)
        results.append(token_fact_result(mint=token['mint'],create=compact.get('create',{}),opening=compact.get('opening',{}),migration=compact.get('migration',{}),lifecycle=compact.get('lifecycle',{}),fx=compact.get('fx',{}),actionability=facts.get('actionability',{})))
    if not dry_run: complete_run(run_id,run_state_db)
    return {"runtime_version": TOKEN_DATA_PRODUCTION_RUNTIME_VERSION, "request_dag_version": REQUEST_DAG_VERSION,
            "run_id": run_id, "dry_run": dry_run, "plans": plans, "results":results}
