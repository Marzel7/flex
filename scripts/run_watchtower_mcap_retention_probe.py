#!/usr/bin/env python3
"""Run exactly the frozen three-request DEV-014 MCAP retention probe."""
from __future__ import annotations
import hashlib,json,math,os
from pathlib import Path
from typing import Any,Mapping
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding,ProviderTransportOutcome

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/audits/dev014_watchtower_mcap_retention_probe_20261009.v1.json'
MAX_BYTES=1_000_000
PROBES=(
 ('RECENT_0_7D','9JtPfLbN32CszHmstoXYdWaazFLaoxhfqf9mKgQMpump',1791476568,1791480168),
 ('MID_30_60D','8epBCVHVZHdiAYweM9vAF57ngKtGVLC9k7oRTh2pump',1788884142,1788887742),
 ('OLDER_61_90D','EaHKRd62bxJBP6VMwoeWLy1uYKkwSX92ux2gQ7JBpump',1785132103,1785135703),
)
ALLOW={'x-ratelimit-limit','x-ratelimit-remaining','x-ratelimit-reset','x-birdeye-cu','x-compute-units'}
def canon(x:Any)->bytes:return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(x:Any)->str:return hashlib.sha256(canon(x)).hexdigest()
def pos(x:Any)->float|None:
 try: x=float(x)
 except (TypeError,ValueError): return None
 return x if math.isfinite(x) and x>0 else None
def normalize(o:ProviderTransportOutcome,a:int,b:int):
 if not isinstance(o,ProviderTransportOutcome):return [],{'category':'INVALID_TRANSPORT'}
 if o.status_code!=200 or o.error_state:return [],{'category':'HTTP_OR_TRANSPORT_FAILURE','http_status':o.status_code}
 if not isinstance(o.payload,Mapping) or o.payload.get('success') is not True:return [],{'category':'INVALID_PROVIDER_SUCCESS'}
 data=o.payload.get('data'); items=data.get('items') if isinstance(data,Mapping) else None
 if not isinstance(items,list):return [],{'category':'UNSUPPORTED_RESPONSE_SHAPE'}
 rows=[]; prior=None; lower=a//60*60
 for i,item in enumerate(items):
  if not isinstance(item,Mapping):return [],{'category':'UNSUPPORTED_ROW','index':i}
  raw=next((item[k] for k in ('unixTime','unix_time','timestamp') if k in item),None)
  try: ts=int(raw)
  except (TypeError,ValueError):return [],{'category':'INVALID_TIMESTAMP','index':i}
  vals=[pos(next((item[k] for k in ks if k in item),None)) for ks in (('o','open'),('h','high'),('l','low'),('c','close'))]
  if ts%60 or ts<lower or ts>=b:return [],{'category':'INVALID_TIMESTAMP','index':i}
  if prior is not None and ts<=prior:return [],{'category':'DUPLICATE_OR_OUT_OF_ORDER','index':i}
  if any(v is None for v in vals):return [],{'category':'INVALID_MCAP_OHLC','index':i}
  op,hi,lo,cl=vals
  if lo>min(op,hi,cl) or hi<max(op,lo,cl):return [],{'category':'INCONSISTENT_MCAP_OHLC','index':i}
  rows.append((ts,op,hi,lo,cl)); prior=ts
 return rows,None
def main():
 if OUT.exists():raise SystemExit('PROBE_ALREADY_EXECUTED')
 if not os.getenv('BIRDEYE','').strip():raise SystemExit('BIRDEYE_CREDENTIAL_REQUIRED')
 t=BirdeyeProductionBinding(credential_label='BIRDEYE'); records=[]
 for band,mint,start,end in PROBES:
  params={'address':mint,'chart_type':'mcap','currency':'usd','type':'1m','mode':'range','padding':'false','time_from':start,'time_to':end}
  o=t({'endpoint':'/defi/v3/ohlcv','request_parameters':params}); rows,fail=normalize(o,start,end)
  # A range starts at the first whole candle *at or after* the requested
  # second, not the preceding partial minute.  This keeps coverage accounting
  # independent of the caller's non-aligned launch timestamp.
  first_expected=((start+59)//60)*60; expected=list(range(first_expected,(end//60)*60+1,60)); got={r[0] for r in rows}; missing=[x for x in expected if x not in got]
  leading=[]; trailing=[]; internal=[]
  if got:
   first,last=min(got),max(got); leading=[x for x in missing if x<first]; trailing=[x for x in missing if x>last]; internal=[x for x in missing if first<x<last]
  else: leading=missing
  rec={'age_band':band,'mint':mint,'requested_window':{'time_from':start,'time_to':end,'interval':'1m'},'request_identity':digest({'mint':mint,'params':params,'probe':'DEV014_RETENTION_V1'}),'provider':'BIRDEYE','http_status':o.status_code,'provider_success':fail is None,'mcap_ohlc_valid':fail is None,'failure':fail,'returned_candle_count':len(rows),'expected_bucket_count':len(expected),'first_returned_timestamp':min(got) if got else None,'last_returned_timestamp':max(got) if got else None,'leading_missing_bucket_timestamps':leading,'internal_missing_bucket_timestamps':internal,'trailing_missing_bucket_timestamps':trailing,'coverage_percent':100*len(rows)/len(expected),'response_metadata':{str(k).lower():str(v) for k,v in o.response_headers.items() if str(k).lower() in ALLOW}}
  rec['evidence_identity']=digest(rec)
  records.append(rec)
 artifact={'artifact_type':'DEV014_WATCHTOWER_MCAP_RETENTION_PROBE','version':1,'research_class':'NON_CANONICAL_PROVIDER_RETENTION_QUALIFICATION','request_contract':{'endpoint':'/defi/v3/ohlcv','max_requests':3,'concurrency':1,'retries':0,'pagination':False,'fallback':False,'raw_provider_payload_retention':False},'records':records,'retention':{'aggregate_max_bytes':MAX_BYTES,'raw_candle_retention':False,'unbounded_growth_paths':0}}
 raw=json.dumps(artifact,sort_keys=True,indent=2)+'\n'
 if len(raw.encode())>MAX_BYTES:raise ValueError('ARTIFACT_BOUND_EXCEEDED')
 OUT.write_text(raw);print(json.dumps({'output':str(OUT),'bytes':len(raw.encode()),'request_count':len(records)},sort_keys=True))
if __name__=='__main__':main()
