"""Pure, provider-free analysis of retained Birdeye price OHLC candles."""
from __future__ import annotations
from typing import Any, Iterable

def parse_ohlc(payload: dict[str,Any]) -> list[dict[str,Any]]:
    out=[]
    for x in (payload.get('data') or {}).get('items') or []:
        try: out.append({'timestamp':int(x['unix_time']),'open':float(x['o']),'high':float(x['h']),'low':float(x['l']),'close':float(x['c']),'volume':x.get('v'),'volume_usd':x.get('v_usd'),'interval':x.get('type')})
        except (KeyError,TypeError,ValueError):continue
    return [x for _,x in sorted({x['timestamp']:x for x in out}.items())]

def lifecycle(candles:Iterable[dict[str,Any]], *, migration_time:int|None, resolution_seconds:int=1, horizon_complete:bool=False) -> dict[str,Any]:
    c=sorted(candles,key=lambda x:x['timestamp'])
    base={'evidence_status':'QUALIFIED' if c else 'INSUFFICIENT_EVIDENCE','first_price_candle_time':c[0]['timestamp'] if c else None,'interval_used':c[0].get('interval') if c else None}
    if not c:return base
    peak=max(c,key=lambda x:(x['high'],-x['timestamp'])); p=peak['high']; result={**base,'peak_price_usd':p,'peak_time':peak['timestamp'],'peak_source':'OBSERVED_CANDLE_HIGH_PEAK','peak_close_price_usd':peak['close'],'time_migration_to_peak_seconds':peak['timestamp']-migration_time if migration_time is not None else None}
    thresholds={50:.5,80:.2,90:.1,95:.05,99:.01}; hits={}
    for k,f in thresholds.items():hits[k]=next((x for x in c if x['timestamp']>=peak['timestamp'] and x['low']<=p*f),None)
    for k,x in hits.items():result[f'rug_{k}_time']=x['timestamp'] if x else None
    r=hits[95]
    if not r:result.update({'rug_95_status':'NO_WITHIN_QUALIFIED_HORIZON' if horizon_complete else 'INSUFFICIENT_EVIDENCE','same_candle_peak_and_rug_95':False,'peak_to_rug_95_seconds_min':None,'peak_to_rug_95_seconds_max':None,'max_post_rug_recovery_percent':None,'max_post_rug_recovery_timestamp':None,'time_rug_to_max_recovery_seconds':None});return result
    same=r['timestamp']==peak['timestamp']; after=[x for x in c if x['timestamp']>r['timestamp']]; recovery=max(after,key=lambda x:x['high']) if after else None
    result.update({'rug_95_status':'YES','same_candle_peak_and_rug_95':same,'peak_to_rug_95_seconds_min':0 if same else r['timestamp']-peak['timestamp'],'peak_to_rug_95_seconds_max':resolution_seconds if same else r['timestamp']-peak['timestamp']+resolution_seconds,'time_migration_to_rug_95_seconds':r['timestamp']-migration_time if migration_time is not None else None,'max_post_rug_recovery_percent':(recovery['high']/p*100) if recovery else 0.0,'max_post_rug_recovery_timestamp':recovery['timestamp'] if recovery else None,'time_rug_to_max_recovery_seconds':recovery['timestamp']-r['timestamp'] if recovery else None})
    return result
