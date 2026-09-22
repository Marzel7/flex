"""Injected, bounded successor ordering recovery; never performs network I/O."""
import hashlib,json,os
from pathlib import Path
from .early_buyer_bundle_history import OfflineStore,digest,normalize_v2,build_v2

VERSION='EARLY_BUYER_ORDER_RECOVERY_V1'
NETWORK_MAX=150*1024*1024
def sha(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def slots(record): return sorted({x['slot'] for x in record['early_buys']})
def select(records):
    rows=sorted(((len(slots(r)),r['mint'],r) for r in records)); med=sorted(x[0] for x in rows)[len(rows)//2]
    picks=[min(rows),min(rows,key=lambda x:(abs(x[0]-med),x[1])),max(rows)]
    return [('LOW_SLOT',picks[0][2]),('MEDIAN_SLOT',picks[1][2]),('HIGH_SLOT',picks[2][2])]
def manifest(records,source_manifest,population_mode='THREE_ROLE_SAMPLE'):
    chosen=select(records) if population_mode=='THREE_ROLE_SAMPLE' else [('FULL_FROZEN_POPULATION',r) for r in sorted(records,key=lambda x:x['mint'])]
    if population_mode=='FULL_FROZEN_POPULATION' and (len(chosen)!=12 or len({r['mint'] for _,r in chosen})!=12 or sum(len(slots(r)) for _,r in chosen)!=165): raise RuntimeError('FULL_FROZEN_POPULATION_INVALID')
    m={'version':VERSION,'population_mode':population_mode,'original_manifest_sha256':source_manifest,'identity_version':'EVENT_SCOPED_V1','contract':'EARLY_HISTORY_ORDERING_V1','selected':[{'role':role,'mint':r['mint'],'slots':slots(r),'source_digest':digest(r),'source_logical_id':r.get('logical_id'),'event_count':len(r['early_buys'])} for role,r in chosen]};m['sha256']=sha(m);return m
def match_block(slot,signatures,body):
    payload=json.loads(body); block=payload.get('result') or payload
    # Solana getBlock(transactionDetails='signatures') returns the ordered
    # signature array directly; normalize it to the same index matcher.
    if 'signatures' in block:
        xs=block.get('signatures')
        if not isinstance(xs,list) or any(not isinstance(x,str) for x in xs): raise RuntimeError('MALFORMED_BLOCK_SIGNATURES')
        txs=[{'transaction':{'signatures':[x]}} for x in xs]
    elif 'transactions' in block:
        txs=block.get('transactions')
        if not isinstance(txs,list): raise RuntimeError('MALFORMED_BLOCK_TRANSACTIONS')
    else: raise RuntimeError('UNSUPPORTED_BLOCK_RESPONSE_SHAPE')
    found={}
    for i,t in enumerate(txs):
        for s in ((t.get('transaction') or {}).get('signatures') or []):
            if s in signatures:
                if s in found: raise RuntimeError('DUPLICATE_SIGNATURE_IN_BLOCK')
                found[s]={'slot':slot,'transaction_index':i,'response_digest':hashlib.sha256(body).hexdigest(),'provenance':'HELIUS_GETBLOCK_EXACT_SLOT'}
    missing=set(signatures)-set(found)
    if missing: raise RuntimeError('RETAINED_SIGNATURE_NOT_FOUND')
    return found
def atomic(path,data):
    raw=json.dumps(data,sort_keys=True,separators=(',',':'));tmp=path.with_suffix('.tmp');tmp.write_text(raw);os.replace(tmp,path)
class RecoveryExecutor:
 def __init__(self,records,source_manifest,out,transport,max_blocks=19,network_max=NETWORK_MAX,transaction_details='full',population_mode='THREE_ROLE_SAMPLE'):
  self.records={r['mint']:r for r in records};self.manifest=manifest(records,source_manifest,population_mode);self.out=Path(out);self.transport=transport;self.max=max_blocks;self.network_max=network_max;self.details=transaction_details;self.cp=self.out/'checkpoint.json';self.store=OfflineStore(self.out/'successors');self.state=json.loads(self.cp.read_text()) if self.cp.exists() else {'manifest':self.manifest['sha256'],'population_mode':population_mode,'completed':[],'slots':{},'bytes':0,'network_max':network_max,'transactionDetails':transaction_details,'budget_version':'ORDERING_RECOVERY_NETWORK_BUDGET_V1'}
  if self.state['manifest']!=self.manifest['sha256']:raise RuntimeError('RECOVERY_MANIFEST_MISMATCH')
 def save(self): self.out.mkdir(parents=True,exist_ok=True);atomic(self.cp,self.state)
 def run(self):
  for e in self.manifest['selected']:
   m=e['mint'];r=self.records[m]
   if m in self.state['completed']:continue
   if len(e['slots'])>self.max:raise RuntimeError('RECOVERY_BLOCK_CEILING')
   maps={}
   for s in e['slots']:
    if s in self.state['slots'].get(m,{}):maps.update(self.state['slots'][m][s]);continue
    if self.state['bytes']>=self.network_max:
     self.state['hold_reason']='FAIL_NETWORK_BUDGET';self.save();raise RuntimeError('FAIL_NETWORK_BUDGET')
    sigs={x['signature'] for x in r['early_buys'] if x['slot']==s};status,body,_=self.transport({'method':'getBlock','slot':s,'transactionDetails':self.details})
    if status!=200:raise RuntimeError('PROVIDER_FAILURE')
    self.state['wire_bytes_received']=self.state.get('wire_bytes_received',self.state['bytes'])+len(body)
    if self.state['bytes']+len(body)>self.network_max:
     self.state['hold_reason']='FAIL_NETWORK_BUDGET';self.save();raise RuntimeError('FAIL_NETWORK_BUDGET')
    got=match_block(s,sigs,body);self.state['bytes']+=len(body);self.state['slots'].setdefault(m,{})[s]=got;self.save();maps.update(got)
   trades=[{**x,'transaction_index':maps[x['signature']]['transaction_index']} for x in r['early_buys']]
   n=normalize_v2(trades,r['creator'],r['launch']['block_time'],mint=m);succ=build_v2(r,n);succ.update({'successor_version':VERSION,'source_record_digest':digest(r),'recovery_manifest_sha256':self.manifest['sha256'],'recovered_ordering':maps})
   d=self.store.commit(succ)
   if digest(self.store.read(m))!=digest(succ):raise RuntimeError('RECOVERY_READBACK_FAILED')
   self.state['completed'].append(m);self.state.setdefault('successors',{})[m]=d;self.save()
  return self.state
