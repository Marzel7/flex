"""Provider-free durable control plane for manifest-driven opening-history runs."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from uuid import uuid4
class ManifestError(RuntimeError): pass
class DurableOpeningHistoryExecutor:
 def __init__(self, manifest_path:Path, expected_sha:str, state_path:Path, failure_injector=None):
  from .failure_injection import FailureInjector
  self.m=manifest_path;self.e=expected_sha;self.s=state_path;self.failure_injector=failure_injector or FailureInjector()
 def verify(self):
  raw=self.m.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=self.e: raise ManifestError('MANIFEST_IDENTITY_FAILURE')
  d=json.loads(raw); sizes=[len(x['mints']) for x in d['batches']]
  qualification=d.get('schema_version')=='OPENING_HISTORY_BATCH_EXECUTOR_QUALIFICATION_3_V1'
  if qualification:
   if len(d['rows'])!=3 or len({x['mint'] for x in d['rows']})!=3 or sizes!=[3]: raise ManifestError('MANIFEST_IDENTITY_FAILURE')
   return d
  if len(d['rows'])!=399 or len({x['mint'] for x in d['rows']})!=399 or sizes!=[50]*7+[49]: raise ManifestError('MANIFEST_IDENTITY_FAILURE')
  if d['reconciliation']!={'already_qualified':23,'denominator':428,'remaining_executable':399,'sum':428,'tier_d':6}: raise ManifestError('POPULATION_RECONCILIATION_FAILURE')
  if d['budgets']['getTransaction_hard']!=399 or d['budgets']['getBlock_hard']!=439 or d['budgets']['hard_wire_bytes']!=7*1024**3: raise ManifestError('MANIFEST_IDENTITY_FAILURE')
  return d
 def initialize(self):
  d=self.verify(); state={'schema_version':'OPENING_HISTORY_DURABLE_EXECUTOR_V1','execution_manifest_sha256':self.e,'run_state':'READY','current_batch_id':1,'next_manifest_row_index':0,'completed_row_count':0,'getTransaction_logical_calls':0,'getBlock_initial_logical_calls':0,'getBlock_extension_logical_calls':0,'extension_blocks_remaining':40,'wire_bytes_received':0,'accepted_compact_bytes':0,'scratch_last_verified_zero':True,'checkpoint_sequence':1,'batch_sizes':[len(x['mints']) for x in d['batches']],'rows':{x['mint']:'PENDING_CREATE_SLOT' for x in d['rows']}}; self.commit(state);return state
 def commit(self,state):
  state['checkpoint_digest']=hashlib.sha256(json.dumps({k:v for k,v in state.items() if k!='checkpoint_digest'},sort_keys=True,separators=(',',':')).encode()).hexdigest();self.s.write_text(json.dumps(state,sort_keys=True,separators=(',',':')));assert json.loads(self.s.read_text())['checkpoint_digest']==state['checkpoint_digest']
 def runtime(self, durable_dir:Path, *, hard_durable_ceiling:int|None=None):
  """Create an offline-only, bounded run state.

  The immutable manifest supplies the ceiling.  A narrower explicit value is
  allowed only for offline qualification; production uses its manifest bound.
  """
  manifest=self.verify()
  ceiling=manifest['budgets']['hard_wire_bytes'] if hard_durable_ceiling is None else hard_durable_ceiling
  if ceiling<0: raise ManifestError('INVALID_DURABLE_BUDGET')
  state=self.initialize();state.update({'run_id':str(uuid4()),'run_state':'RUNNING','ledger':[],'extension_reservations':{},'compact_handoffs':{},'opening_reductions':{},'canonical_lifecycle':{},'terminal_results':{},'durable_bytes_written':0,'hard_durable_ceiling':ceiling,'scratch_dir':str(durable_dir/'scratch'),'durable_dir':str(durable_dir/'records'),'implementation_failures':[],'canonical_readbacks':{},'raw_retention':0,'semantic_contract_version':'OPENING_PRICE_HISTORY_V1','state_machine_version':'OPENING_HISTORY_DURABLE_EXECUTOR_V1'});Path(state['scratch_dir']).mkdir(parents=True,exist_ok=True);Path(state['durable_dir']).mkdir(parents=True,exist_ok=True);self.commit(state);return state
 def resume(self):
  """Load a durable checkpoint without replaying an unreduced response."""
  state=json.loads(self.s.read_text())
  if state.get('execution_manifest_sha256')!=self.e: raise ManifestError('CHECKPOINT_MANIFEST_MISMATCH')
  digest=state.pop('checkpoint_digest',None); actual=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  state['checkpoint_digest']=digest
  if digest!=actual: raise ManifestError('CHECKPOINT_INTEGRITY_FAILURE')
  stranded=[x for x in state.get('ledger',[]) if x['response_consumed'] and not x['accepted'] and x['terminal_effect'] is None and x.get('handoff_digest') not in state.get('compact_handoffs',{})]
  if stranded:
   state['run_state']='HOLD';state['hold_reasons']=['NON_REPLAYABLE_RESPONSE_BOUNDARY'];self.commit(state)
   raise ManifestError('NON_REPLAYABLE_RESPONSE_BOUNDARY')
  for mint,reduction in state.get('opening_reductions',{}).items():
   if mint not in state.get('canonical_lifecycle',{}) and state['rows'].get(mint)=='OPENING_REDUCTION_COMMITTED':
    template=reduction.get('canonical_input'); encoded=json.dumps(template,sort_keys=True,separators=(',',':')).encode() if template is not None else b''
    if hashlib.sha256(encoded).hexdigest()!=reduction.get('canonical_input_content_digest'): state['run_state']='HOLD';state['hold_reasons']=['CANONICAL_INPUT_DIGEST_MISMATCH'];self.commit(state);raise ManifestError('CANONICAL_INPUT_DIGEST_MISMATCH')
    if hashlib.sha256(json.dumps({k:v for k,v in reduction.items() if k!='checkpoint_content_digest'},sort_keys=True,separators=(',',':')).encode()).hexdigest()!=reduction.get('checkpoint_content_digest'): state['run_state']='HOLD';self.commit(state);raise ManifestError('OPENING_REDUCTION_CHECKPOINT_INCONSISTENCY')
    if any(x not in state.get('compact_handoffs',{}) for x in reduction.get('source_handoffs',[])): state['run_state']='HOLD';state['hold_reasons']=['SOURCE_HANDOFF_DIGEST_MISMATCH'];self.commit(state);raise ManifestError('SOURCE_HANDOFF_DIGEST_MISMATCH')
    self.prepare_canonical(state,mint,template)
  for mint,lifecycle in state.get('canonical_lifecycle',{}).items(): self._reconcile_canonical(state,mint,lifecycle)
  return state
 def _reconcile_canonical(self,state,mint,lifecycle):
  reduction=state.get('opening_reductions',{}).get(mint); expected=lifecycle.get('expected_digest'); size=lifecycle.get('expected_size')
  if not reduction or lifecycle.get('source_opening_reduction_digest')!=reduction.get('checkpoint_content_digest'):
   state['run_state']='HOLD';state['hold_reasons']=['OPENING_REDUCTION_CHECKPOINT_INCONSISTENCY'];self.commit(state);raise ManifestError('OPENING_REDUCTION_CHECKPOINT_INCONSISTENCY')
  target=Path(lifecycle['target']); status=lifecycle['state']; raw=json.dumps(lifecycle.get('record'),sort_keys=True,separators=(',',':')).encode() if lifecycle.get('record') is not None else None
  if status in {'CANONICAL_PREPARED','CANONICAL_TEMP_WRITTEN'}:
   if raw is None or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=expected: state['run_state']='HOLD';self.commit(state);raise ManifestError('CANONICAL_REBUILD_UNAVAILABLE')
   temp=Path(lifecycle.get('temp') or str(target.with_suffix('.tmp')))
   if not temp.exists() or temp.stat().st_size!=size or hashlib.sha256(temp.read_bytes()).hexdigest()!=expected: temp.write_bytes(raw)
   lifecycle.update({'state':'CANONICAL_TEMP_WRITTEN','temp':str(temp),'temp_digest':expected,'temp_size':size});self.commit(state);temp.replace(target);lifecycle.update({'state':'CANONICAL_COMMITTED','committed_digest':expected,'committed_size':size});self.commit(state);status='CANONICAL_COMMITTED'
  if status in {'CANONICAL_COMMITTED','CANONICAL_READBACK_VERIFIED','ROW_TERMINAL'}:
   if not target.exists(): state['run_state']='HOLD';state['hold_reasons']=['CANONICAL_COMMIT_MISSING'];self.commit(state);raise ManifestError('CANONICAL_COMMIT_MISSING')
   if target.stat().st_size!=size or hashlib.sha256(target.read_bytes()).hexdigest()!=expected: state['run_state']='HOLD';state['hold_reasons']=['CANONICAL_COMMIT_CONFLICT'];self.commit(state);raise ManifestError('CANONICAL_COMMIT_CONFLICT')
   if status=='CANONICAL_COMMITTED': lifecycle['state']='CANONICAL_READBACK_VERIFIED';state['canonical_readbacks'][mint]=expected;self.commit(state)
   if lifecycle['state']=='CANONICAL_READBACK_VERIFIED': lifecycle['state']='ROW_TERMINAL';state['rows'][mint]='TERMINAL';state['terminal_results'][mint]=state['terminal_results'].get(mint,'OPENING_HISTORY_QUALIFIED');state['completed_row_count']=max(state['completed_row_count'],sum(x=='TERMINAL' for x in state['rows'].values()));self.commit(state)
 def commit_compact_handoff(self,state,entry,kind,payload,max_bytes):
  raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
  if len(raw)>max_bytes: raise ManifestError('COMPACT_HANDOFF_TOO_LARGE')
  record={'schema_version':'OPENING_HISTORY_COMPACT_HANDOFF_V1','kind':kind,'source_response_digest':entry['response_digest'],'source_wire_bytes':entry['wire_bytes'],'payload':payload}
  digest=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest();record['content_digest']=digest
  state['compact_handoffs'][digest]=record;entry['handoff_digest']=digest;state['accepted_compact_bytes']+=len(raw);state['rows'][entry['mint']]=kind+'_HANDOFF_COMMITTED';self.commit(state);self.failure_injector.hit('AFTER_CREATE_HANDOFF_COMMITTED' if kind=='CREATE' else 'AFTER_BLOCK_HANDOFF_COMMITTED');return record
 def commit_opening_reduction(self,state,mint,create,result,canonical_input=None):
  events=[{k:e.get(k) for k in ('ordinal','buyer','signature','slot','transaction_index','event_index','quote_lamports','token_raw','token_decimals','price_status','valuation_status')} for e in result.get('opening_events',[])]
  payload={'schema_version':'OPENING_REDUCTION_CHECKPOINT_V1','run_id':state['run_id'],'mint':mint,'create':create,'B1_B2_B3':events,'opening_amount_sol':result.get('opening_amount_sol'),'fingerprint_status':result.get('fingerprint_status'),'post_B3_valuation':result.get('post_B3_implied_valuation_sol'),'post_B3_valuation_status':result.get('post_B3_valuation_status'),'reducer_version':'OPENING_HISTORY_EXECUTION_ADAPTER_V1','source_handoffs':[x.get('handoff_digest') for x in state['ledger'] if x['mint']==mint and x.get('handoff_digest')],'canonical_input_schema_version':'OPENING_HISTORY_CANONICAL_RECORD_V1','canonical_input':canonical_input}
  payload['canonical_input_content_digest']=hashlib.sha256(json.dumps(canonical_input,sort_keys=True,separators=(',',':')).encode()).hexdigest() if canonical_input is not None else None
  raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
  if len(raw)>64*1024: raise ManifestError('OPENING_REDUCTION_TOO_LARGE')
  digest=hashlib.sha256(raw).hexdigest();payload['checkpoint_content_digest']=digest;state['opening_reductions'][mint]=payload;state['rows'][mint]='OPENING_REDUCTION_COMMITTED';self.commit(state);self.failure_injector.hit('AFTER_OPENING_REDUCTION_COMMITTED');return payload
 def prepare_canonical(self,state,mint,record):
  raw=json.dumps(record,sort_keys=True,separators=(',',':')).encode();target=Path(state['durable_dir'])/(mint+'.json');expected=hashlib.sha256(raw).hexdigest();state['canonical_lifecycle'][mint]={'state':'CANONICAL_PREPARED','target':str(target),'expected_digest':expected,'expected_size':len(raw),'record':record,'source_opening_reduction_digest':(state['opening_reductions'].get(mint) or {}).get('checkpoint_content_digest')};self.commit(state);return state['canonical_lifecycle'][mint]
 def dispatch(self,state,mint,phase,method,request):
  self.failure_injector.hit('BEFORE_CREATE_DISPATCH_INTENT' if phase=='CREATE_TX' else 'BEFORE_BLOCK_DISPATCH_INTENT')
  if state['run_state'] in {'HOLD','FAILED','COMPLETE','BATCH_GATE'}: raise ManifestError('DISPATCH_BLOCKED')
  if mint not in state['rows'] or method not in {'getTransaction','getBlock'}: raise ManifestError('INVALID_DISPATCH')
  limits={'getTransaction':399,'getBlock':439}
  used=state['getTransaction_logical_calls'] if method=='getTransaction' else state['getBlock_initial_logical_calls']+state['getBlock_extension_logical_calls']
  if used>=limits[method]: state['run_state']='HOLD';self.commit(state);raise ManifestError('CALL_CEILING_EXCEEDED')
  row=list(state['rows']).index(mint)
  if method=='getTransaction': state['getTransaction_logical_calls']+=1
  elif phase.startswith('EXTENSION'): state['getBlock_extension_logical_calls']+=1
  else: state['getBlock_initial_logical_calls']+=1
  payload={'request_schema_version':'OPENING_HISTORY_PROVIDER_REQUEST_V1','provider_method':method,'params':request.get('params') if isinstance(request,dict) and 'params' in request else request}
  encoded=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
  if len(encoded)>4096: raise ManifestError('REQUEST_PAYLOAD_TOO_LARGE')
  state['checkpoint_sequence']+=1
  entry={'ledger_schema_version':'OPENING_HISTORY_ACTION_LEDGER_V2','run_id':state['run_id'],'batch_id':row//50+1,'row_index':row,'mint':mint,'phase':phase,'provider_method':method,'logical_call_number':used+1,'attempt_number':1,'request_payload':payload,'request_payload_sha256':hashlib.sha256(encoded).hexdigest(),'request_identity_digest':hashlib.sha256(encoded).hexdigest(),'dispatch_intent_persisted':True,'transport_invoked':False,'response_consumed':False,'wire_bytes':0,'response_digest':None,'accepted':False,'terminal_effect':None,'checkpoint_sequence':state['checkpoint_sequence']};state['ledger'].append(entry);state['rows'][mint]=phase+'_DISPATCH_INTENT';self.commit(state);self.failure_injector.hit('AFTER_CREATE_DISPATCH_INTENT' if phase=='CREATE_TX' else 'AFTER_BLOCK_DISPATCH_INTENT');return entry
 def consume(self,state,entry,response:bytes):
  entry['transport_invoked']=True;entry['response_consumed']=True;entry['wire_bytes']=len(response);entry['response_digest']=hashlib.sha256(response).hexdigest();state['wire_bytes_received']+=len(response)
  if state['wire_bytes_received']>7*1024**3: state['run_state']='HOLD';entry['terminal_effect']='NETWORK_BUDGET_EXCEEDED';self.commit(state);raise ManifestError('NETWORK_BUDGET_EXCEEDED')
  state['rows'][entry['mint']]=entry['phase']+'_RESPONSE_CONSUMED';self.commit(state);self.failure_injector.hit('AFTER_CREATE_RESPONSE_CONSUMED' if entry['phase']=='CREATE_TX' else ('AFTER_EXTENSION_RESPONSE_CONSUMED' if entry['phase'].startswith('EXTENSION') else 'AFTER_BLOCK_RESPONSE_CONSUMED'))
 def accept(self,state,entry,phase):
  if not entry['response_consumed']: raise ManifestError('RESPONSE_NOT_CONSUMED')
  entry['accepted']=True;entry['terminal_effect']=phase;state['rows'][entry['mint']]=phase;self.commit(state)
 def reserve_extension(self,state,mint=None):
  if state['extension_blocks_remaining']<=0: state['run_state']='HOLD';self.commit(state);raise ManifestError('EXTENSION_POOL_EXHAUSTED')
  state['extension_blocks_remaining']-=1
  if mint is not None:
   ordinal=40-state['extension_blocks_remaining']
   state['rows'][mint]=f'PENDING_EXTENSION_{ordinal}'
  self.commit(state);self.failure_injector.hit('AFTER_EXTENSION_RESERVATION')
 def reserve_extension_record(self,state,mint,create_slot,ordinal,logical_action_id):
  if ordinal not in {1,2} or (ordinal==2 and f'{mint}:1' not in state['extension_reservations']): raise ManifestError('INVALID_EXTENSION_RESERVATION')
  key=f'{mint}:{ordinal}'
  if key in state['extension_reservations']: raise ManifestError('DUPLICATE_EXTENSION_RESERVATION')
  before=state['extension_blocks_remaining']
  if before<=0: raise ManifestError('EXTENSION_POOL_EXHAUSTED')
  row=list(state['rows']).index(mint);record={'schema_version':'OPENING_HISTORY_EXTENSION_RESERVATION_V1','run_id':state['run_id'],'batch_id':row//50+1,'row_index':row,'mint':mint,'extension_ordinal':ordinal,'reserved_slot':create_slot+ordinal,'parent_create_slot':create_slot,'logical_action_id':logical_action_id,'provider_method':'getBlock','request_phase':f'EXTENSION_{ordinal}','reservation_sequence':len(state['extension_reservations'])+1,'reserved_at_checkpoint_sequence':state['checkpoint_sequence']+1,'extension_pool_before':before,'extension_pool_after':before-1,'status':'RESERVED'}
  record['reservation_digest']=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest();state['extension_reservations'][key]=record;state['extension_blocks_remaining']=before-1;state['rows'][mint]=f'PENDING_EXTENSION_{ordinal}';self.commit(state);self.failure_injector.hit('AFTER_EXTENSION_RESERVATION');return record
 def bind_extension_dispatch(self,state,reservation,entry):
  payload=entry.get('request_payload') or {}
  if payload.get('provider_method')!='getBlock' or payload.get('params',[None])[0]!=reservation['reserved_slot'] or entry['mint']!=reservation['mint'] or entry['phase']!=reservation['request_phase']: raise ManifestError('EXTENSION_RESERVATION_REQUEST_MISMATCH')
  entry.update({'reservation_id':f"{reservation['mint']}:{reservation['extension_ordinal']}",'reservation_digest':reservation['reservation_digest'],'extension_ordinal':reservation['extension_ordinal'],'reserved_slot':reservation['reserved_slot'],'parent_create_slot':reservation['parent_create_slot'],'logical_action_id':reservation['logical_action_id']});reservation['status']='DISPATCH_INTENT_BOUND';self.commit(state)
 def commit_record(self,state,mint,record):
  raw=json.dumps(record,sort_keys=True,separators=(',',':')).encode()
  if len(raw)>512*1024: raise ManifestError('CANONICAL_RECORD_TOO_LARGE')
  if state['durable_bytes_written']+len(raw)>state['hard_durable_ceiling']: state['run_state']='HOLD';self.commit(state);raise ManifestError('DURABLE_BUDGET_EXCEEDED')
  self.prepare_canonical(state,mint,record);target=Path(state['durable_dir'])/(mint+'.json');tmp=target.with_suffix('.tmp');expected=hashlib.sha256(raw).hexdigest();self.failure_injector.hit('AFTER_CANONICAL_PREPARED');tmp.write_bytes(raw);state['canonical_lifecycle'][mint].update({'state':'CANONICAL_TEMP_WRITTEN','temp':str(tmp),'temp_digest':hashlib.sha256(tmp.read_bytes()).hexdigest(),'temp_size':tmp.stat().st_size});self.commit(state);self.failure_injector.hit('AFTER_CANONICAL_TEMP_WRITE');self.failure_injector.hit('AFTER_CANONICAL_TEMP_WRITTEN');tmp.replace(target);state['canonical_lifecycle'][mint].update({'state':'CANONICAL_COMMITTED','committed_digest':expected,'committed_size':len(raw)});self.commit(state);self.failure_injector.hit('AFTER_CANONICAL_COMMIT');self.failure_injector.hit('AFTER_CANONICAL_COMMITTED')
  if target.read_bytes()!=raw: state['run_state']='HOLD';self.commit(state);raise ManifestError('READBACK_FAILURE')
  state['durable_bytes_written']+=len(raw);state['canonical_readbacks'][mint]=hashlib.sha256(raw).hexdigest();state['canonical_lifecycle'][mint]['state']='CANONICAL_READBACK_VERIFIED';self.commit(state);self.failure_injector.hit('AFTER_CANONICAL_READBACK');self.failure_injector.hit('AFTER_CANONICAL_READBACK_VERIFIED');state['rows'][mint]='TERMINAL';state['canonical_lifecycle'][mint]['state']='ROW_TERMINAL';state['terminal_results'][mint]=record.get('terminal_status','OPENING_HISTORY_QUALIFIED');state['completed_row_count']+=1;self.commit(state);self.failure_injector.hit('AFTER_ROW_TERMINAL')
 def batch_gate(self,state,batch_id):
  start=sum(state['batch_sizes'][:batch_id-1]); batch=list(state['rows'])[start:start+state['batch_sizes'][batch_id-1]]
  # This is intentionally conditional: the boundary only exists once the
  # active batch has already reached its row-terminal durable state.
  if all(state['rows'][m]=='TERMINAL' for m in batch): self.failure_injector.hit('BEFORE_BATCH_GATE')
  reasons=[]
  try: self.verify()
  except ManifestError: reasons.append('MANIFEST')
  self.failure_injector.hit('DURING_BATCH_GATE')
  digest=state.get('checkpoint_digest'); actual=hashlib.sha256(json.dumps({k:v for k,v in state.items() if k!='checkpoint_digest'},sort_keys=True,separators=(',',':')).encode()).hexdigest()
  if digest!=actual: reasons.append('CHECKPOINT')
  if not all(state['rows'][m]=='TERMINAL' for m in batch): reasons.append('ROWS_NOT_TERMINAL')
  if any(Path(state['scratch_dir']).iterdir()): reasons.append('SCRATCH_RESIDUAL')
  if state['wire_bytes_received']>7*1024**3: reasons.append('WIRE')
  if state['durable_bytes_written']>state['hard_durable_ceiling']: reasons.append('DURABLE')
  if state['raw_retention']!=0: reasons.append('RAW_RETENTION')
  if state['implementation_failures']: reasons.append('IMPLEMENTATION_FAILURE')
  if state['getTransaction_logical_calls']>399 or state['getBlock_initial_logical_calls']+state['getBlock_extension_logical_calls']>439: reasons.append('CALL_CEILING')
  state['run_state']='HOLD' if reasons else ('COMPLETE' if batch_id==len(state['batch_sizes']) else 'BATCH_GATE');state['batch_gate']={'batch_id':batch_id,'decision':'HOLD' if reasons else 'CONTINUE','reasons':reasons};
  if not reasons and batch_id<len(state['batch_sizes']): state['current_batch_id']=batch_id+1;state['run_state']='READY'
  self.commit(state)
  if state['batch_gate']['decision']=='CONTINUE': self.failure_injector.hit('AFTER_CONTINUE_COMMIT')
  else: self.failure_injector.hit('AFTER_HOLD_COMMIT')
  return state['batch_gate']
