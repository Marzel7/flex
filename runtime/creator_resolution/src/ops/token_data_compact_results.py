"""Raw-free durable compact outputs for token-data request finalization."""
from __future__ import annotations
import json, time
from pathlib import Path
from .provider_request_durability import durable_write

TOKEN_DATA_COMPACT_RESULT_RETENTION_VERSION='TOKEN_DATA_COMPACT_RESULT_RETENTION_V1'
ROOT=Path('docs/audits/token_data_compact_results')

def _path(run_id, mint): return ROOT / run_id / f'{mint}.json'
def persist_compact_request_result(*,run_id,request,mint,terminal_state,fact_state,canonical_result,provider_status,parser_version='TOKEN_DATA_FACT_RESULT_V1'):
    path=_path(run_id,mint); path.parent.mkdir(parents=True,exist_ok=True)
    data=json.loads(path.read_text()) if path.exists() else {'contract_version':TOKEN_DATA_COMPACT_RESULT_RETENTION_VERSION,'run_id':run_id,'mint':mint,'results':[]}
    data['results'].append({'execution_request_id':request['request_identity_hash'],'semantic_request_id':request['request_identity_hash'],'request_family':request['semantic_family'],'terminal_request_state':terminal_state,'fact_state':fact_state,'provider_status':provider_status,'parser_version':parser_version,'completed_at':int(time.time()),'canonical_result':canonical_result})
    durable_write(path,data); return path
def load_compact_request_result(*,run_id,mint):
    path=_path(run_id,mint); return json.loads(path.read_text()) if path.exists() else None
def load_run_compact_results(run_id): return [json.loads(p.read_text()) for p in sorted((ROOT/run_id).glob('*.json'))] if (ROOT/run_id).exists() else []
def finalize_token_from_compact_results(*,run_id,mint):
    """Offline projection: only durable canonical request results are consulted."""
    record=load_compact_request_result(run_id=run_id,mint=mint) or {'results':[]}; facts={}
    for item in record['results']:
        family=item['request_family']; value=item['canonical_result']
        facts[family]={'fact_state':item['fact_state'],'value':value,'terminal_request_state':item['terminal_request_state']}
    return {'schema_version':'TOKEN_DATA_FACT_RESULT_V1','run_id':run_id,'mint':mint,'facts':facts}
