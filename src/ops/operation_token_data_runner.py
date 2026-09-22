"""Generic durable execution and request-DAG planning for token-data facts."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from .provider_request_durability import execute_durable_provider_request
from .provider_request_durability import durable_write, plan
from .token_data_orchestration import AuthorizationEnvelope
from .token_data_fact_adapters import (
    create_fact, opening_fact, migration_fact, lifecycle_fact, fx_fact,
    token_fact_result,
)

RUNNER_VERSION='OPERATION_TOKEN_DATA_RUNNER_V1'
REQUEST_DAG_VERSION='TOKEN_DATA_REQUEST_DAG_V1'

CHAIN_CREATE_TRANSACTION='CHAIN_CREATE_TRANSACTION'
OPENING_SEQUENCE_BLOCK='OPENING_SEQUENCE_BLOCK'
CHAIN_MIGRATION_TRANSACTION='CHAIN_MIGRATION_TRANSACTION'
SOL_USD_FX='SOL_USD_FX'
PRICE_HISTORY='PRICE_HISTORY'
_QUALIFIED={'FACT_QUALIFIED','QUALIFIED'}
_TERMINAL={'FACT_QUALIFIED','QUALIFIED','FACT_INSUFFICIENT_EVIDENCE','FACT_UNRECOVERABLE_HISTORICAL_STATE','FACT_PROVIDER_BLOCKED','FACT_RATE_LIMITED_TERMINAL','FACT_NOT_APPLICABLE','FACT_NOT_EVALUATED_DEPENDENCY_BLOCKED'}

def _qualified(facts: Mapping[str, Any], name: str) -> bool:
    value=facts.get(name)
    return value in _QUALIFIED or isinstance(value,Mapping) and value.get('state') in _QUALIFIED

def _terminal(facts: Mapping[str, Any], name: str) -> bool:
    value=facts.get(name)
    return value in _TERMINAL or isinstance(value,Mapping) and value.get('state') in _TERMINAL

def _value(facts: Mapping[str, Any], name: str) -> Any:
    value=facts.get(name)
    return value.get('value') if isinstance(value,Mapping) and 'value' in value else value

def _request(*,family: str,token: Mapping[str, Any],provider: str,endpoint: str,
             parameters: Mapping[str, Any],dependencies: Sequence[str],outputs: Sequence[str]) -> dict[str, Any]:
    """Construct a compact logical request; this function never dispatches."""
    return {'request_family':family,'token':str(token['mint']),'dependency_facts':list(dependencies),
            'provider':provider,'method_endpoint':endpoint,'request_parameters':dict(parameters),
            'authorization_requirement':{'provider':provider,'method_endpoint':endpoint},
            'durability_identity_inputs':{'provider':provider,'method_endpoint':endpoint,
                'semantic_family':family,'token':str(token['mint']),'parameters':dict(parameters)},
            'fact_outputs':list(outputs)}

def _authorized(nodes: Sequence[Mapping[str, Any]], envelope: AuthorizationEnvelope, already_planned: int) -> bool:
    return (already_planned+len(nodes)<=envelope.maximum_provider_calls
        and (envelope.maximum_physical_attempts is None or already_planned+len(nodes)<=envelope.maximum_physical_attempts)
        and all(node['provider'] in envelope.allowed_providers and node['method_endpoint'] in envelope.allowed_methods_or_endpoints
                and (envelope.allowed_fact_families is None or node['request_family'] in envelope.allowed_fact_families) for node in nodes))

def plan_token_data_requests(*,token: Mapping[str, Any],requested_fact_families: Sequence[str],
                             retained_facts: Mapping[str, Any],authorization_envelope: AuthorizationEnvelope,
                             provider_request_timestamp: int,already_planned: int=0) -> dict[str, Any]:
    """Materialize only currently constructible generic request nodes.

    Callers feed compact outputs back as retained facts and invoke this again.
    At most one opening block is planned so creation-slot-block.v3 remains the
    sole owner of the existing bounded stop semantics.
    """
    nodes=[]
    wants_opening=bool({'OPENING_STATE','THEORETICAL_ENTRY','OPENING_EXECUTION_FINGERPRINT'}.intersection(requested_fact_families))
    wants_lifecycle=bool({'PRICE_HISTORY','PEAK','TERMINAL_VALUE','MAX_PROVEN_DRAWDOWN'}.intersection(requested_fact_families))
    wants_migration=bool({'MIGRATION','MIGRATION_POOL_VALUATION'}.intersection(requested_fact_families))
    wants_fx='SOL_USD_FX' in requested_fact_families
    if (wants_opening or wants_lifecycle) and not _terminal(retained_facts,'CREATE_FACT'):
        signature=token.get('create_signature')
        if signature:nodes.append(_request(family=CHAIN_CREATE_TRANSACTION,token=token,provider='Helius JSON-RPC',endpoint='getTransaction',parameters={'signature':signature,'encoding':'jsonParsed','maxSupportedTransactionVersion':0},dependencies=[],outputs=['CREATE_FACT','CREATE_SLOT','CREATE_TIMESTAMP']))
    if wants_migration and not _terminal(retained_facts,'MIGRATION'):
        signature=token.get('migration_signature')
        if signature:nodes.append(_request(family=CHAIN_MIGRATION_TRANSACTION,token=token,provider='Helius JSON-RPC',endpoint='getTransaction',parameters={'signature':signature,'encoding':'jsonParsed','maxSupportedTransactionVersion':0},dependencies=[],outputs=['MIGRATION','MIGRATION_SLOT','MIGRATION_TIMESTAMP','FIRST_PUMPSWAP_POOL_MC_SOL']))
    create_slot=_value(retained_facts,'CREATE_SLOT'); opening_done=_terminal(retained_facts,'OPENING_STATE') or retained_facts.get('OPENING_STOPPED') is True
    if wants_opening and isinstance(create_slot,int) and not opening_done:
        next_slot=_value(retained_facts,'OPENING_NEXT_SLOT');next_slot=create_slot if next_slot is None else next_slot
        if isinstance(next_slot,int) and create_slot<=next_slot<create_slot+8:nodes.append(_request(family=OPENING_SEQUENCE_BLOCK,token=token,provider='Helius JSON-RPC',endpoint='getBlock',parameters={'slot':next_slot,'transactionDetails':'full','rewards':False},dependencies=['CREATE_SLOT'],outputs=['OPENING_STATE','OPENING_NEXT_SLOT','OPENING_STOPPED']))
    create_timestamp=_value(retained_facts,'CREATE_TIMESTAMP')
    if wants_lifecycle and isinstance(create_timestamp,int) and not _terminal(retained_facts,'PRICE_HISTORY'):
        nodes.append(_request(family=PRICE_HISTORY,token=token,provider='Birdeye',endpoint='/defi/v3/ohlcv',parameters={'address':token['mint'],'type':'1m','chart_type':'mcap','currency':'usd','mode':'range','time_from':create_timestamp,'time_to':min(create_timestamp+86400,provider_request_timestamp)},dependencies=['CREATE_TIMESTAMP'],outputs=['PRICE_HISTORY','PEAK','TERMINAL_VALUE','MAX_PROVEN_DRAWDOWN']))
    fx_target=_value(retained_facts,'FX_TARGET_TIMESTAMP')
    if wants_fx and isinstance(fx_target,int) and not _terminal(retained_facts,'SOL_USD_FX'):
        nodes.append(_request(family=SOL_USD_FX,token=token,provider='Birdeye',endpoint='/defi/v3/ohlcv',parameters={'address':'So11111111111111111111111111111111111111112','type':'1m','chart_type':'price','currency':'usd','mode':'range','time_from':fx_target-60,'time_to':fx_target},dependencies=['FX_TARGET_TIMESTAMP'],outputs=['SOL_USD_FX']))
    if not _authorized(nodes,authorization_envelope,already_planned):return {'state':'RUN_BLOCKED_AUTHORIZATION_REQUIRED','requests':[]}
    return {'state':'READY','requests':nodes,'retained_fact_request_suppression':True}

def _durability_spec(node: Mapping[str, Any]) -> dict[str, Any]:
    return {**node['durability_identity_inputs'],'parameters':node['request_parameters']}

def run_operation_token_data_playbook(*, manifest_path: Path, manifest: dict, provider_bindings, compact):
    """Execute planned requests through durable bindings; no operation branches."""
    outcomes=[]
    for index,row in enumerate(manifest['requests']):
        binding=provider_bindings[(row['provider'],row['method_endpoint'])]
        outcomes.append(execute_durable_provider_request(manifest_path=manifest_path,manifest=manifest,index=index,provider_call=lambda r=row,b=binding:b(r),compact=lambda payload,r=row:compact(r,payload)))
    return outcomes

def run_token_data_request_dag(*,manifest_path: Path,run_id: str,token: Mapping[str, Any],
                               requested_fact_families: Sequence[str],retained_facts: dict[str, Any],
                               authorization_envelope: AuthorizationEnvelope,provider_request_timestamp: int,
                               provider_bindings,compact: Callable[[Mapping[str, Any],Any],tuple[dict[str, Any],Mapping[str, Any]]]) -> dict[str, Any]:
    """Execute planner-produced nodes only through the durable executor.

    ``compact`` owns canonical adapter ingestion and returns an outcome together
    with fact updates.  This loop owns neither transport nor methodology.
    """
    executed=0;outcomes=[]
    while True:
        planned=plan_token_data_requests(token=token,requested_fact_families=requested_fact_families,
            retained_facts=retained_facts,authorization_envelope=authorization_envelope,
            provider_request_timestamp=provider_request_timestamp,already_planned=executed)
        if planned['state']!='READY':return {'state':planned['state'],'outcomes':outcomes,'retained_facts':retained_facts}
        nodes=planned['requests']
        if not nodes:return {'state':'COMPLETE','outcomes':outcomes,'retained_facts':retained_facts}
        manifest=plan(run_id=run_id,requests=[_durability_spec(node) for node in nodes],planned_at=provider_request_timestamp)
        batch_path=manifest_path.with_name(f'{manifest_path.stem}.{executed}{manifest_path.suffix}')
        durable_write(batch_path,manifest)
        for index,node in enumerate(nodes):
            binding=provider_bindings[(node['provider'],node['method_endpoint'])]
            def compact_node(payload,node=node):
                outcome,updates=compact(node,payload);retained_facts.update(updates);return outcome
            outcome=execute_durable_provider_request(manifest_path=batch_path,manifest=manifest,index=index,
                provider_call=lambda node=node,binding=binding:binding(node),compact=compact_node)
            outcomes.append({'node':node,'outcome':outcome});executed+=1


def compose_token_data_fact_result(*, mint, create_response, create_signature,
                                   migration_response, migration_signature, pool,
                                   opening_profile, opening_events, lifecycle_response,
                                   fx_response, fx_target_timestamp, actionability):
    """Compose compact canonical facts after durable acquisition has completed.

    The caller supplies already-derived actionability evidence because its
    linkage/exclusivity contract is intentionally owned outside token data.
    No provider, operation, or venue branch is introduced here.
    """
    return token_fact_result(
        mint=mint,
        create=create_fact(create_response, create_signature, mint),
        opening=opening_fact(opening_profile, opening_events),
        migration=migration_fact(migration_response, migration_signature, mint, pool),
        lifecycle=lifecycle_fact(lifecycle_response),
        fx=fx_fact(fx_response, fx_target_timestamp),
        actionability=actionability,
    )
