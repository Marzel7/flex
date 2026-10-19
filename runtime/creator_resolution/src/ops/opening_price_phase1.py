"""Offline Phase-1 anchor manifest and fact-decision layer."""
import hashlib,json
FACTS={'CREATION':('CREATE_EVENT_DECODE','CURVE_IDENTITY','INITIALIZATION_RESERVES','TOKEN_DECIMALS','TOKEN_SUPPLY','SUPPLY_BASIS','PROTOCOL_FORMULA_IDENTITY'),'FIRST_NON_CREATOR_BUY':('EXECUTED_TOKEN_AMOUNT','EXECUTED_QUOTE_AMOUNT','SWAP_SEMANTICS','PROTOCOL_FEE_SEMANTICS','EVENT_BOUND_BALANCE_DELTAS','PRE_TRADE_RESERVES','POST_TRADE_RESERVES','TOKEN_DECIMALS','TOKEN_SUPPLY','SUPPLY_BASIS')}
STATES={'NOT_REQUIRED','OFFLINE_MATERIALIZATION_REQUIRED','PROVIDER_TRANSACTION_REQUIRED','PROVIDER_BLOCK_REQUIRED','PROVIDER_ACCOUNT_STATE_REQUIRED','TERMINAL_INSUFFICIENT'}
def h(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def manifest(successors,births):
 b={x['mint']:x for x in births}; es=[]
 for r in sorted(successors,key=lambda x:x['mint']):
  x=b[r['mint']]; first=r['early_buys'][0]
  es += [{'anchor_id':h(['CREATION',r['mint'],x['create_signature']]),'mint':r['mint'],'anchor_type':'CREATION','signature':x['create_signature'],'slot':None,'reference_event_id':h(['CREATE',r['mint'],x['create_signature']]),'facts':FACTS['CREATION'],'source_digest':h(x)},{'anchor_id':h(['FIRST_NON_CREATOR_BUY',r['mint'],first['signature']]),'mint':r['mint'],'anchor_type':'FIRST_NON_CREATOR_BUY','signature':first['signature'],'slot':first['slot'],'logical_event_id':h(['EARLY',r['mint'],first['signature'],first['slot'],first['transaction_index'],first['event_index']]),'transaction_index':first['transaction_index'],'event_index':first['event_index'],'buyer':first['buyer'],'facts':FACTS['FIRST_NON_CREATOR_BUY'],'source_digest':h(r)}]
 m={'version':'OPENING_PRICE_PHASE1_V1','entries':es,'ceilings':{'transaction':1,'block':1,'account_state':1},'forbidden':['history','pagination','neighbour_slot','actionable_price']};m['sha256']=h(m)
 if len(es)!=24 or len({x['mint'] for x in es})!=12:raise RuntimeError('ANCHOR_MANIFEST_INVALID')
 return m
def decide(anchor,fact,local='MISSING',transaction_evaluated=False,account_known=False):
 if fact not in anchor['facts']:return 'NOT_REQUIRED'
 if local=='QUALIFIED':return 'NOT_REQUIRED'
 if local=='DECODABLE':return 'OFFLINE_MATERIALIZATION_REQUIRED'
 if not transaction_evaluated:return 'PROVIDER_TRANSACTION_REQUIRED'
 if fact in ('PRE_TRADE_RESERVES','POST_TRADE_RESERVES','INITIALIZATION_RESERVES'):return 'PROVIDER_BLOCK_REQUIRED'
 if account_known:return 'PROVIDER_ACCOUNT_STATE_REQUIRED'
 return 'TERMINAL_INSUFFICIENT'
