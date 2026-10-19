"""Pure durable-state continuation classifier for opening-history runs."""
from __future__ import annotations
VERSION='OPENING_HISTORY_RESUME_DISPATCHER_V1'
def validate_extension_dispatch_binding(reservation,entry,payload):
 try:
  return reservation['mint']==entry['mint'] and reservation['request_phase']==entry['phase'] and reservation['provider_method']==entry['provider_method'] and payload['provider_method']=='getBlock' and payload['params'][0]==reservation['reserved_slot'] and entry.get('reservation_digest')==reservation['reservation_digest'] and entry.get('extension_ordinal')==reservation['extension_ordinal'] and entry.get('reserved_slot')==reservation['reserved_slot'] and reservation['status']=='DISPATCH_INTENT_BOUND'
 except (KeyError,IndexError,TypeError): return False
def next_action(state,mint):
 if state.get('run_state')=='HOLD': return 'HOLD'
 row=state['rows'].get(mint)
 if row=='TERMINAL': return 'TERMINAL_NOOP'
 if row=='OPENING_REDUCTION_COMMITTED' or row in {'CANONICAL_PREPARED','CANONICAL_TEMP_WRITTEN','CANONICAL_COMMITTED','CANONICAL_READBACK_VERIFIED'}: return 'CONTINUE_CANONICAL'
 entries=[x for x in state.get('ledger',[]) if x['mint']==mint]
 if row in {'CREATE_SLOT_QUALIFIED','PENDING_CREATE_BLOCK'}:
  entries=[x for x in entries if x['phase']=='CREATE_BLOCK']
  if not entries: return 'DISPATCH_CREATE_BLOCK'
 if row and row.startswith('PENDING_EXTENSION'):
  ordinal=row.rsplit('_',1)[1]; entries=[x for x in entries if x['phase']==f'EXTENSION_{ordinal}']
  if not entries: return 'DISPATCH_EXTENSION'
 if row in {'PENDING_CREATE_SLOT','CREATE_TX_DISPATCH_INTENT'}: entries=[x for x in entries if x['phase']=='CREATE_TX']
 if entries:
  last=entries[-1]
  if last['transport_invoked'] and not last['response_consumed']: return 'FAIL_CLOSED'
  if last['response_consumed'] and not last.get('accepted'):
   if last.get('handoff_digest') not in state.get('compact_handoffs',{}): return 'FAIL_CLOSED'
   return 'CONTINUE_FROM_CREATE_HANDOFF' if last['phase']=='CREATE_TX' else ('CONTINUE_FROM_EXTENSION_HANDOFF' if last['phase'].startswith('EXTENSION') else 'CONTINUE_FROM_BLOCK_HANDOFF')
  if last['dispatch_intent_persisted']:
   if last['phase'].startswith('EXTENSION'):
    reservation=state.get('extension_reservations',{}).get(f"{mint}:{last.get('extension_ordinal')}")
    return 'DISPATCH_EXTENSION' if validate_extension_dispatch_binding(reservation,last,last.get('request_payload',{})) else 'FAIL_CLOSED'
   return 'DISPATCH_CREATE' if last['phase']=='CREATE_TX' else 'DISPATCH_CREATE_BLOCK'
 if row=='PENDING_CREATE_SLOT': return 'DISPATCH_CREATE'
 if row in {'CREATE_SLOT_QUALIFIED','PENDING_CREATE_BLOCK'}: return 'DISPATCH_CREATE_BLOCK'
 if row and row.startswith('PENDING_EXTENSION'): return 'DISPATCH_EXTENSION'
 return 'FAIL_CLOSED'
