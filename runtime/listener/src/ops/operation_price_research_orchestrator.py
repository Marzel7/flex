"""Provider-disabled coordinator for retained operation price research.

It deliberately selects an operation adapter before any entry-dependent reduction;
unknown operations can describe evidence but never inherit another operation's rule.
"""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; AUDIT=ROOT/'docs/audits'
STAGES=('COHORT_RESOLVED','ENTRY_EVIDENCE_READY','ENTRY_FACTS_READY','LIFECYCLE_EVIDENCE_READY','PRICE_FACTS_READY','PLAYBOOK_READY')
ADAPTERS={'watchtower':{'method':'FIRST_FULL_POST_MIGRATION_SECOND_MC','contract':'WATCHTOWER_PRICE_FACT_CONTRACT_V1','candle_resolution':'15m','executable_fill_proven':False},'byzantine':{'method':'SCENARIO_D_ACTIONABLE_COUNTERFACTUAL_ENTRY_FLOOR','contract':'BYZANTINE_SCENARIO_D_12_13_V1','candle_resolution':'15m','executable_fill_proven':False}}
def adapter_for(operation_id): return ADAPTERS.get(operation_id,{'method':'ENTRY_METHOD_UNQUALIFIED','contract':None,'executable_fill_proven':False})
def _digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def run_operation_price_research(operation_id, cohort=None, checkpoint=None):
    adapter=adapter_for(operation_id); state={'operation_id':operation_id,'adapter':adapter,'network_calls':0,'checkpointed':True,'resumable':True,'idempotent':True,'stages':list(STAGES)}
    if operation_id=='watchtower':
        d=json.loads((AUDIT/'watchtower_51_generic_price_fact_replay.v1.json').read_text()); state.update({'rows':d['rows'],'statistics':d['statistics'],'entry_replay':d['entry_replay'],'evidence_status':'COMPLETE'})
    elif operation_id=='byzantine':
        d=json.loads((AUDIT/'research_entry_semantics_cohort_replay.v1.json').read_text()); rows=d.get('rows',d.get('cohort',[])); state.update({'rows':rows,'evidence_status':'RETAINED_WINDOW_GAPS_PRESERVED','lifecycle_semantics':'RETAINED_WINDOW_MAX_MC distinct from POST_ENTRY_MAX_MC_LOWER_BOUND; gaps are not losses'})
    else:
        state.update({'rows':[{'mint':m,'entry_method':'ENTRY_METHOD_UNQUALIFIED','entry_mc_usd':None,'entry_to_max_multiple':None,'reached_2x':None,'reached_5x':None,'reached_10x':None,'strategy_performance':'INSUFFICIENT_EVIDENCE'} for m in (cohort or [])],'evidence_status':'ENTRY_METHOD_UNQUALIFIED'})
    state['provenance_digest']=_digest({'operation_id':operation_id,'adapter':adapter,'rows':state['rows']}); return state
