"""Frozen Byzantine opening-cluster seed adapter; no listener or provider work."""
from __future__ import annotations
import hashlib,json
from decimal import Decimal
from pathlib import Path
from typing import Mapping
from src.ops.pumpfun_opening_impulse import market_cap_sol

VERSION='BYZANTINE_OPENING_CLUSTER_V1'; SUPPLY_RAW=1_000_000_000_000_000; DECIMALS=6
def _id(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def seed(path: str) -> dict:
 d=json.loads(Path(path).read_text()); wallets=tuple(d['cluster_wallets']); return {'version':VERSION,'wallets':wallets,'source_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'cluster_id':_id({'version':VERSION,'wallets':wallets})}
def classify(action: Mapping, frozen_seed: Mapping) -> dict:
 if action.get('action_type')!='BUY' or action.get('actor') not in frozen_seed['wallets']:
  return {'membership':'CLUSTER_MEMBERSHIP_UNAVAILABLE','recurrence':'RECURRENCE_NOT_ESTABLISHED'}
 return {'membership':'CLUSTER_MEMBERSHIP_QUALIFIED','recurrence':'RECURRENT_CLUSTER_QUALIFIED','cluster_id':frozen_seed['cluster_id'],'classifier_version':VERSION,'membership_provenance':{'seed_sha256':frozen_seed['source_sha256'],'expected_wallet_position':frozen_seed['wallets'].index(action['actor'])+1}}
def spot_reference(action: Mapping) -> dict:
 p=json.loads(action['payload']) if isinstance(action.get('payload'),str) else action; s=(p.get('event_post_state') or {})
 if not {'post_virtual_sol_reserves','post_virtual_token_reserves'} <= set(s):return {'grade':'SCENARIO_D_SPOT_REFERENCE_UNAVAILABLE'}
 fdv=market_cap_sol(virtual_sol_reserves=int(s['post_virtual_sol_reserves']),virtual_token_reserves=int(s['post_virtual_token_reserves']),supply_raw=SUPPLY_RAW,decimals=DECIMALS)
 return {'grade':'SCENARIO_D_SPOT_REFERENCE_QUALIFIED','model':'SCENARIO_D_FDV_SOL_V1','supply_raw':SUPPLY_RAW,'decimals':DECIMALS,'fdv_sol':str(fdv),'spot_sol_per_token':str(fdv/Decimal(1_000_000_000)),'action_id':p.get('logical_fact_id')}
