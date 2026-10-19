"""Incremental, transport-free creation-slot-block.v3 state reduction."""
from .birth_anchored_opening_acquisition import actions_from_block, PUMP_TOTAL_SUPPLY_RAW, PUMP_DECIMALS
from .pumpfun_opening_impulse import market_cap_sol

OPENING_SEQUENCE_AGGREGATOR_VERSION='OPENING_SEQUENCE_AGGREGATOR_V1'
MAX_BLOCKS=8

def initialize_opening_sequence(*,mint,create_signature,create_slot,create_timestamp,creator):
 return {'mint':mint,'create_signature':create_signature,'create_slot':create_slot,'create_timestamp':create_timestamp,'creator':creator,'ordered_slots_seen':[],'actions':[],'blocks_consumed':0,'opening_complete':False,'terminal_state':None,'next_required_slot':create_slot}

def ingest_opening_sequence_block(state,*,slot,block_payload):
 if state['opening_complete'] or slot!=state['next_required_slot']: return state
 actions=actions_from_block(block_payload,mint=state['mint'],slot=slot);state['actions'].extend(actions);state['ordered_slots_seen'].append(slot);state['blocks_consumed']+=1
 independent=next((a for a in sorted(state['actions'],key=lambda x:(x['slot'],x['transaction_index'],x['action_index'])) if a['action_type']=='BUY' and a['buyer']!=state['creator']),None)
 if independent:
  state['first_independent_buy_signature']=independent['signature'];state['first_independent_buy_slot']=independent['slot'];state['first_independent_buy_tx_index']=independent['transaction_index'];state['completion_target_slot']=independent['slot']+3
  state['theoretical_first_executable_fdv_sol']=str(market_cap_sol(virtual_sol_reserves=independent['post_virtual_sol_reserves'],virtual_token_reserves=independent['post_virtual_token_reserves'],supply_raw=PUMP_TOTAL_SUPPLY_RAW,decimals=PUMP_DECIMALS))
 if (independent and slot>=state['completion_target_slot']) or state['blocks_consumed']>=MAX_BLOCKS:
  state['opening_complete']=True;state['terminal_state']='QUALIFIED' if independent else 'INSUFFICIENT_EVIDENCE_BOUND_EXHAUSTED'
 else: state['next_required_slot']=slot+1
 return state

def finalize_opening_sequence(state):
 return {'state':state['terminal_state'],'first_independent_buy_signature':state.get('first_independent_buy_signature'),'first_independent_buy_index':(state.get('first_independent_buy_slot'),state.get('first_independent_buy_tx_index')) if state.get('first_independent_buy_signature') else None,'theoretical_first_executable_fdv_sol':state.get('theoretical_first_executable_fdv_sol'),'opening_sequence_tx_count':len(state['actions']),'opening_execution_fingerprint':'creation-slot-block.v3','blocks_consumed':state['blocks_consumed']}
