"""Execute one durable continuation without restarting a fresh row."""
import hashlib,json
from .opening_history_resume_dispatcher import next_action,validate_extension_dispatch_binding
from .opening_history_batch_executor import ManifestError

def load_persisted_request(entry):
 payload=entry.get('request_payload')
 if entry.get('ledger_schema_version')!='OPENING_HISTORY_ACTION_LEDGER_V2' or not isinstance(payload,dict): raise ManifestError('EXACT_REQUEST_PAYLOAD_UNAVAILABLE')
 raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
 if len(raw)>4096 or hashlib.sha256(raw).hexdigest()!=entry.get('request_payload_sha256') or entry.get('request_payload_sha256')!=entry.get('request_identity_digest'): raise ManifestError('REQUEST_RECONSTRUCTION_MISMATCH')
 if payload.get('request_schema_version')!='OPENING_HISTORY_PROVIDER_REQUEST_V1' or payload.get('provider_method')!=entry.get('provider_method') or not isinstance(payload.get('params'),list): raise ManifestError('REQUEST_RECONSTRUCTION_MISMATCH')
 return payload['provider_method'],payload['params']

def execute_next_action_from_durable_state(executor,state,row,adapter,transport=None):
 action=next_action(state,row['mint'])
 entries=[x for x in state.get('ledger',[]) if x['mint']==row['mint']]
 handoffs=[state['compact_handoffs'][x['handoff_digest']]['payload'] for x in entries if x.get('handoff_digest') in state.get('compact_handoffs',{})]
 if action=='HOLD': raise ManifestError('DISPATCH_BLOCKED')
 if action=='FAIL_CLOSED': raise ManifestError('NON_REPLAYABLE_RESPONSE_BOUNDARY')
 if action=='TERMINAL_NOOP': return action
 if action=='CONTINUE_CANONICAL': executor.resume(); return action
 if action=='DISPATCH_CREATE':
  entry=entries[-1]
  if entry['phase']!='CREATE_TX' or entry['transport_invoked'] or entry['response_consumed'] or transport is None: raise ManifestError('TRANSPORT_OUTCOME_AMBIGUOUS')
  method,params=load_persisted_request(entry); raw=transport(method,params); entry['transport_invoked']=True; executor.commit(state); executor.failure_injector.hit('AFTER_CREATE_TRANSPORT_RETURN'); executor.consume(state,entry,raw); payload=json.loads(raw); create=adapter.normalize_create_response(row,payload); executor.commit_compact_handoff(state,entry,'CREATE',create,16*1024); executor.accept(state,entry,'PENDING_CREATE_BLOCK'); return {'action':action,'create':create}
 if action=='DISPATCH_CREATE_BLOCK':
  entry=entries[-1]
  if entry['phase']!='CREATE_BLOCK' or entry['transport_invoked'] or entry['response_consumed'] or transport is None: raise ManifestError('TRANSPORT_OUTCOME_AMBIGUOUS')
  method,params=load_persisted_request(entry); raw=transport(method,params); entry['transport_invoked']=True; executor.commit(state); executor.failure_injector.hit('AFTER_BLOCK_TRANSPORT_RETURN'); executor.consume(state,entry,raw); compact=adapter.normalize_block_response(row,params[0],json.loads(raw),len(raw)); executor.commit_compact_handoff(state,entry,'BLOCK',compact,256*1024); return {'action':action,'block':compact}
 if action=='DISPATCH_EXTENSION':
  entry=entries[-1]; reservation=state['extension_reservations'].get(entry.get('reservation_id'))
  if entry['transport_invoked'] or entry['response_consumed'] or transport is None or not validate_extension_dispatch_binding(reservation,entry,entry.get('request_payload',{})): raise ManifestError('EXTENSION_RESERVATION_REQUEST_MISMATCH')
  method,params=load_persisted_request(entry); raw=transport(method,params); entry['transport_invoked']=True; executor.commit(state); executor.failure_injector.hit('AFTER_EXTENSION_TRANSPORT_RETURN'); executor.consume(state,entry,raw); reservation['status']='RESPONSE_CONSUMED'; executor.commit(state); compact=adapter.normalize_block_response(row,params[0],json.loads(raw),len(raw)); executor.commit_compact_handoff(state,entry,'BLOCK',compact,256*1024); reservation['status']='HANDOFF_COMMITTED'; executor.commit(state); executor.failure_injector.hit('AFTER_EXTENSION_HANDOFF_COMMITTED'); return {'action':action,'block':compact}
 creates=[x for x in handoffs if x.get('signature')==row['create_signature']]
 blocks=[x for x in handoffs if 'target_relevant_transactions' in x]
 if action=='CONTINUE_FROM_CREATE_HANDOFF':
  create=adapter.continue_from_create_handoff(row,creates[-1]); executor.accept(state,entries[-1],'PENDING_CREATE_BLOCK')
  existing=[x for x in state['ledger'] if x['mint']==row['mint'] and x['phase']=='CREATE_BLOCK']
  if not existing:
   method,params=adapter.build_block_request(row,create['slot'],'CREATE_SLOT'); executor.dispatch(state,row['mint'],'CREATE_BLOCK',method,{'params':params})
  return {'action':action,'create':create}
 if action in {'CONTINUE_FROM_BLOCK_HANDOFF','CONTINUE_FROM_EXTENSION_HANDOFF'}:
  if not creates: raise ManifestError('CREATE_HANDOFF_MISSING')
  result=adapter.reduce_opening_from_handoffs(row,creates[-1],blocks); executor.failure_injector.hit('AFTER_SEMANTIC_REDUCTION'); executor.failure_injector.hit('AFTER_B1_B2_B3_SELECTION'); executor.failure_injector.hit('AFTER_OPENING_AMOUNT')
  if result.get('needs_extension'): return {'action':action,'result':result}
  record=adapter.record(row,creates[-1],result);executor.commit_opening_reduction(state,row['mint'],creates[-1],result,record);executor.commit_record(state,row['mint'],record);return {'action':action,'record':record}
 raise ManifestError('CONTINUATION_ACTION_NOT_YET_EXECUTABLE_'+action)
