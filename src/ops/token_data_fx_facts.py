from .token_data_lifecycle_facts import result_items,as_number
def fx_summary(r,target):
 p=[(int(t),c) for x in result_items(r) if (t:=x.get('unixTime',x.get('unix_time',x.get('time',x.get('timestamp'))))) is not None and (c:=as_number(x.get('c'))) is not None];e=[x for x in p if x[0]<=target and target-x[0]<=60]
 if not e:return {'state':'INSUFFICIENT_EVIDENCE_NO_QUALIFIED_SOL_USD','target_timestamp':target}
 t,c=max(e);return {'state':'QUALIFIED','target_timestamp':target,'sol_usd_price':c,'sol_usd_source':'Birdeye /defi/v3/ohlcv 1m SOL/USD','sol_usd_observation_timestamp':t,'sol_usd_offset_seconds':target-t}
