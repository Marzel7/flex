"""Bounded provider adapter; transport is injected, no implicit network I/O."""
import hashlib,json
from .early_buyer_bundle_history import plan_escalation
MAX_BYTES=150*1024*1024
def digest(b):return hashlib.sha256(b).hexdigest()
def birdeye_request(mint,after_time,before_time):
    return {'path':'/defi/v3/token/txs','headers':('X-API-KEY','x-chain: solana'),'params':{'address':mint,'tx_type':'buy','limit':20,'after_time':after_time,'before_time':before_time}}
def parse_birdeye(body,mint):
    d=json.loads(body);items=(d.get('data') or {}).get('items') or [];trades=[]
    for i,x in enumerate(items):
        trades.append({'signature':x.get('tx_hash'),'buyer':x.get('owner'),'asserted_buyer':x.get('buyer'),'slot':x.get('block_number'),'block_time':x.get('block_unix_time'),'transaction_index':x.get('tx_index'),'event_index':x.get('ins_index'),'provider_order':i,'sol_spent':x.get('volume'),'token_amount':x.get('token_amount'),'execution_price':x.get('price'),'provider':'BIRDEYE','evidence_id':digest(body)})
    return {'provider_mint':(d.get('data') or {}).get('address'),'trades':trades,'response_digest':digest(body),'response_bytes':len(body)}
def acquire_one(transport,mint,after_time,before_time,counters):
    if counters.get('birdeye',0)>=2: raise RuntimeError('BUDGET_EXHAUSTED')
    # Attempts are per-mint durable state.  Starting from the persisted value is
    # what prevents an interrupted 429 sequence from acquiring a fourth try.
    req=birdeye_request(mint,after_time,before_time);attempt=counters.get('birdeye_attempts',0)
    while attempt<3:
        attempt+=1;status,body,headers=transport(req)
        counters['birdeye_attempts']=attempt
        if status==200:
            counters['birdeye']=counters.get('birdeye',0)+1;counters['bytes']=counters.get('bytes',0)+len(body)
            if counters['bytes']>MAX_BYTES: raise RuntimeError('FAIL_NETWORK_BUDGET')
            return parse_birdeye(body,mint)
        if status!=429 or attempt==3: raise RuntimeError('PROVIDER_FAILURE')
    raise RuntimeError('PROVIDER_FAILURE')
def helius_fallback(transport,mint,candidate_id,missing_fact,action,counters):
    call='helius_transaction' if action=='getTransaction' else 'helius_block' if action=='getBlock' else None
    if not call: raise ValueError('UNSUPPORTED_HELIUS_ACTION')
    plan=plan_escalation(candidate_id,mint,'BUNDLE_CANDIDATE',missing_fact,call,True,counters)
    if plan['decision']!='AUTHORIZED': return {'plan':plan,'response':None}
    status,body,_=transport({'method':action,'mint':mint});counters[call]=counters.get(call,0)+1;counters['bytes']=counters.get('bytes',0)+len(body)
    if counters['bytes']>MAX_BYTES: raise RuntimeError('FAIL_NETWORK_BUDGET')
    if status!=200: raise RuntimeError('PROVIDER_FAILURE')
    return {'plan':plan,'response_digest':digest(body),'response_bytes':len(body),'response':json.loads(body)}
