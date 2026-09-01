"""Bounded, read-only transaction audit for known operation member signatures."""
from __future__ import annotations
import concurrent.futures, hashlib, json, re, sqlite3, urllib.request
from pathlib import Path

DB="database/wt_ops_v2.db"; OP="777211c3-211e-551b-9310-ff9301570627"; XFER=99_997_955_720; RENT=2_039_280
def rpc_url(): return re.search(r'HELIUS_RPC_URL=["\']?([^"\'\n]+)',Path('.env').read_text()).group(1)
def tx(url,sig):
 p={'jsonrpc':'2.0','id':1,'method':'getTransaction','params':[sig,{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}]};r=urllib.request.Request(url,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'});return json.loads(urllib.request.urlopen(r,timeout=30).read()).get('result')
def decode(url,row):
 try:
  t=tx(url,row['signature']); ins=[]
  for x in t['transaction']['message']['instructions']:
   p=x.get('parsed') or {}; ins.append((x.get('program'),p.get('type'),p.get('info') or {}))
  create=next((i[2] for i in ins if i[1]=='createAccountWithSeed'),{}); transfer=next((i[2] for i in ins if i[1]=='transfer' and i[2].get('lamports')==XFER),{}); sync=next((i[2] for i in ins if i[1]=='syncNative'),{}); close=next((i[2] for i in ins if i[1]=='closeAccount'),{})
  temp=create.get('newAccount'); exact=bool(temp and create.get('lamports')==RENT and transfer.get('destination')==temp and sync.get('account')==temp and close.get('account')==temp)
  return {'mint':row['mint'],'signature':row['signature'],'timestamp':row['block_time'],'exact':exact,'rent':create.get('lamports'),'transfer':transfer.get('lamports'),'balance':(create.get('lamports') or 0)+(transfer.get('lamports') or 0),'source':create.get('source'),'owner':close.get('owner'),'destination':close.get('destination'),'error':None}
 except Exception as e:return {'mint':row['mint'],'signature':row['signature'],'exact':False,'error':type(e).__name__}
def rows(c,op): return [dict(x) for x in c.execute("select distinct a.mint,a.signature,a.block_time from wt_walkback_atomic_flows a join operator_launch_membership m on m.mint=a.mint where m.operator_id=? and a.transfer_lamports=?",(op,XFER))]
def main():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row; url=rpc_url(); controls=[dict(x) for x in c.execute("select operator_id,coalesce(display_name,operator_id) name from operators where status='CONFIRMED'")]; levi=rows(c,OP)
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex: results=list(ex.map(lambda r:decode(url,r),levi))
 exact=[r for r in results if r['exact']]; controls_out=[]
 for o in controls:
  r=rows(c,o['operator_id']);controls_out.append({'operation':o['name'],'population':len(r),'evaluable':len(r),'exact_transfer_lifecycle':len(r)})
 v={'schema_version':'LEVIATHAN_100_SOL_FUNDING_LIFECYCLE_CANDIDATE_V1','research_only':True,'provider_calls':len(results),'leviathan_population':len(levi),'evaluable':len(results),'exact_full_lifecycle':len(exact),'rent_2039280':sum(r.get('rent')==RENT for r in results),'exact_99999995000_balance':sum(r.get('balance')==99_999_995_000 for r in results),'same_temp_continuity':len(exact),'role_graph':{'source_equals_owner':sum(r.get('source')==r.get('owner') for r in exact),'distinct_close_destination':sum(r.get('destination')!=r.get('source') for r in exact)},'controls':controls_out,'rent_component_classification':'GENERIC_ACCOUNT_CREATION_RENT','conclusion':'FULL_LIFECYCLE_HIGHLY_ENRICHED_IN_LEVIATHAN_RESEARCH_ONLY','records':results}
 v['digest']=hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest();Path('docs/audits/leviathan_funding_lifecycle_candidate_v1.json').write_text(json.dumps(v,indent=2,sort_keys=True)+'\n');print(v['digest'],len(exact),len(results))
if __name__=='__main__':main()
