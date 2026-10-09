#!/usr/bin/env python3
"""Execute only the frozen, Entry-anchored DEV-014 Batch 3 requests."""
from __future__ import annotations
import hashlib,json,math,os
from pathlib import Path
from typing import Any,Mapping
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding,ProviderTransportOutcome
from src.ops.watchtower_observed_minimum import observed_minima
ROOT=Path(__file__).resolve().parents[1]; PLAN=ROOT/'docs/audits/dev014_watchtower_recent_first_price_forensics_batch_3_allowlist_20261009.v1.json'; POP=ROOT/'docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json'; OUT=ROOT/'docs/audits/dev014_watchtower_recent_first_price_forensics_batch_3_20261009.v1.json'; MAX=1_000_000; HEAD={'x-ratelimit-limit','x-ratelimit-remaining','x-ratelimit-reset','x-birdeye-cu','x-compute-units'}
def canon(x:Any)->bytes:return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def dig(x:Any)->str:return hashlib.sha256(canon(x)).hexdigest()
def num(x:Any)->float|None:
 try:x=float(x)
 except (TypeError,ValueError):return None
 return x if math.isfinite(x) and x>0 else None
def rows(o:ProviderTransportOutcome,start:int,end:int):
 if not isinstance(o,ProviderTransportOutcome) or o.status_code!=200 or o.error_state:return [],{'category':'HTTP_OR_TRANSPORT_FAILURE','http_status':getattr(o,'status_code',0)}
 if not isinstance(o.payload,Mapping) or o.payload.get('success') is not True:return [],{'category':'INVALID_PROVIDER_SUCCESS'}
 d=o.payload.get('data'); xs=d.get('items') if isinstance(d,Mapping) else None
 if not isinstance(xs,list):return [],{'category':'UNSUPPORTED_RESPONSE_SHAPE'}
 answer=[]; previous=None; lower=start//60*60
 for i,x in enumerate(xs):
  if not isinstance(x,Mapping):return [],{'category':'UNSUPPORTED_ROW','index':i}
  raw=next((x[k] for k in ('unixTime','unix_time','timestamp') if k in x),None)
  try:t=int(raw)
  except (ValueError,TypeError):return [],{'category':'INVALID_TIMESTAMP','index':i}
  v=[num(next((x[k] for k in a if k in x),None)) for a in (('o','open'),('h','high'),('l','low'),('c','close'))]
  if t%60 or t<lower or t>=end:return [],{'category':'INVALID_TIMESTAMP','index':i}
  if previous is not None and t<=previous:return [],{'category':'DUPLICATE_OR_OUT_OF_ORDER','index':i}
  if any(y is None for y in v):return [],{'category':'INVALID_MCAP_OHLC','index':i}
  op,hi,lo,cl=v
  if lo>min(op,hi,cl) or hi<max(op,lo,cl):return [],{'category':'INCONSISTENT_MCAP_OHLC','index':i}
  answer.append({'timestamp':t,'open_mc_usd':op,'high_mc_usd':hi,'low_mc_usd':lo,'close_mc_usd':cl});previous=t
 return answer,None
def main():
 if OUT.exists():raise SystemExit('BATCH_3_OUTPUT_ALREADY_EXISTS')
 if not os.getenv('BIRDEYE','').strip():raise SystemExit('BIRDEYE_CREDENTIAL_REQUIRED')
 plan=json.loads(PLAN.read_text()); selected=[x for x in plan['records'] if x['disposition']=='REQUEST']
 if len(selected)>10 or len({x['mint'] for x in selected})!=len(selected):raise SystemExit('INVALID_FROZEN_ALLOWLIST')
 pop={x['mint']:x for x in json.loads(POP.read_text())['launches']}; t=BirdeyeProductionBinding(credential_label='BIRDEYE'); records=[]
 for item in selected:
  mint=item['mint']; start=int(item['anchor_timestamp']); end=start+3600; params={'address':mint,'chart_type':'mcap','currency':'usd','type':'1m','mode':'range','padding':'false','time_from':start,'time_to':end}; o=t({'endpoint':'/defi/v3/ohlcv','request_parameters':params}); got,fail=rows(o,start,end); opening=pop[mint]['evidence']['opening']; rec={'mint':mint,'chronological_rank':item['rank'],'creation_timestamp':item['creation_timestamp'],'anchor':{'type':item['anchor_type'],'timestamp':start,'provenance':item['anchor_provenance']},'qualified_entry_identity':{'timestamp':opening['entry_timestamp'],'mc_usd':opening['entry_mc_usd'],'provenance':opening['provenance']},'requested_window':{'time_from':start,'time_to':end,'interval':'1m'},'request_identity':dig({'mint':mint,'params':params,'batch':'DEV014_BATCH_3'}),'provider':'BIRDEYE','http_status':o.status_code,'provider_metadata':{str(k).lower():str(v) for k,v in o.response_headers.items() if str(k).lower() in HEAD},'normalization_status':'FAILED','failure':fail}
  if fail is None:
   rec['observed_minima']=observed_minima(entry_timestamp=start,entry_mc_usd=float(opening['entry_mc_usd']),candles=[{'timestamp':x['timestamp'],'low_mc_usd':x['low_mc_usd']} for x in got],provider_provenance='BIRDEYE_1M_MCAP')['results']; rec['returned_candle_count']=len(got); rec['normalization_status']='NORMALIZED';rec['failure']=None
  rec['evidence_identity']=dig(rec); records.append(rec)
 artifact={'artifact_type':'DEV014_WATCHTOWER_RECENT_FIRST_PRICE_FORENSICS_BATCH_3','version':1,'provider':'BIRDEYE','research_class':'NON_CANONICAL_HISTORICAL_PRICE_FORENSICS','allowlist_sha256':hashlib.sha256(PLAN.read_bytes()).hexdigest(),'request_contract':plan['request_contract'],'records':records,'skipped_or_blocked':[x for x in plan['records'] if x['disposition']!='REQUEST'],'summary':{'request_count':len(records),'normalized_count':sum(x['normalization_status']=='NORMALIZED' for x in records),'qualified_entry_count':len(records),'research_anchor_count':len(records),'missing_bucket_count':sum(len(v.get('missing_bucket_timestamps') or []) for x in records for v in x.get('observed_minima',{}).values())},'retention':plan['retention']}
 raw=json.dumps(artifact,sort_keys=True,indent=2)+'\n';
 if len(raw.encode())>MAX:raise ValueError('ARTIFACT_BOUND_EXCEEDED')
 OUT.write_text(raw);print(json.dumps({'output':str(OUT),'bytes':len(raw.encode()),**artifact['summary']},sort_keys=True))
if __name__=='__main__':main()
