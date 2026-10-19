"""Pure compact Birdeye lifecycle facts."""
def as_number(v):
 try:return float(v)
 except (TypeError,ValueError):return None
def result_items(r):return ((r or {}).get('data') or {}).get('items') or ((r or {}).get('data') or {}).get('list') or []
def history_summary(r):
 n=[]
 for x in result_items(r):
  t=x.get('unixTime',x.get('unix_time',x.get('time',x.get('timestamp'))));h,l,c=as_number(x.get('h')),as_number(x.get('l')),as_number(x.get('c'))
  if t is not None and h is not None and l is not None and c is not None:n.append((int(t),h,l,c))
 n.sort()
 if not n:return {'state':'INSUFFICIENT_EVIDENCE_NO_USABLE_MCAP_CANDLES'}
 p=max(n,key=lambda x:(x[1],-x[0]));later=[x for x in n if x[0]>p[0]];tr=min(later,key=lambda x:x[2]) if later else None;out={'state':'QUALIFIED','peak_mc':p[1],'peak_timestamp':p[0],'peak_source':'Birdeye market-cap series','peak_completeness':'PEAK_OBSERVED_WINDOW_ONLY','terminal_or_last_observed_mc':n[-1][3],'terminal_timestamp':n[-1][0],'candle_count':len(n)};out.update({'drawdown_trough_mc':tr[2],'drawdown_trough_timestamp':tr[0],'max_proven_drawdown':(p[1]-tr[2])/p[1]} if tr and p[1]>0 else {'drawdown_trough_mc':None,'drawdown_trough_timestamp':None,'max_proven_drawdown':None});return out
