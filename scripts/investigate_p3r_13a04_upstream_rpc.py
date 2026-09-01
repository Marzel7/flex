#!/usr/bin/env python3
"""Strictly bounded, isolated RPC ancestry probe for one frozen P3R candidate."""
import hashlib,json,os,re,sqlite3,time,urllib.request
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/tmp/p3r-clean-20260824T092959Z'); OUT=ROOT/'rpc_13a04_upstream_v3'; DB=Path('database/wt_ops_v2.db')
BASE=ROOT/'behavioural_corpus/p3r_candidate_operational_family_membership.v1.json'; TIER=ROOT/'activity/tiers/p3r_tier1_candidate_membership.v1.json'
TARGET=29_999_990_000; NEAR=25_000
CEIL={'max_additional_depth':3,'max_transactions_per_wallet':4,'max_requests_per_wallet':5,'max_total_requests':60,'max_pagination_depth':1,'max_retries':0,'max_elapsed_seconds':120}
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x): Path(p).write_text(json.dumps(x,sort_keys=True,indent=2)+'\n'); return {'path':str(p),'sha256':sha(p)}
def envurl():
 if os.getenv('HELIUS_TEMP_API_KEY'): return 'https://mainnet.helius-rpc.com/?api-key='+os.getenv('HELIUS_TEMP_API_KEY'),'environment:HELIUS_TEMP_API_KEY'
 e=Path('.env')
 if e.exists():
  text=e.read_text()
  temp=re.search(r'^\s*(?:export\s+)?HELIUS_TEMP_API_KEY\s*=\s*["\']?([^"\'\s]+)',text,re.M)
  if temp: return 'https://mainnet.helius-rpc.com/?api-key='+temp.group(1),'dotenv:HELIUS_TEMP_API_KEY'
 for k in ('HELIUS_RPC_URL','RPC_URL','RPC_URL_2'):
  if os.getenv(k): return os.getenv(k),'environment:'+k
 if e.exists():
  for line in text.splitlines():
   m=re.match(r'\s*(?:export\s+)?(HELIUS_RPC_URL|RPC_URL|RPC_URL_2)\s*=\s*["\']?([^"\'\s]+)',line)
   if m: return m.group(2),'dotenv:'+m.group(1)
  key=re.search(r'^\s*(?:export\s+)?HELIUS_API_KEY\s*=\s*["\']?([^"\'\s]+)',text,re.M)
  if key: return 'https://mainnet.helius-rpc.com/?api-key='+key.group(1),'dotenv:HELIUS_API_KEY'
 return None,'not_configured'
def tx_transfers(tx,wallet):
 found=[]
 def walk(insts):
  for z in insts or []:
   parsed=z.get('parsed') if isinstance(z,dict) else None
   info=(parsed or {}).get('info',{}) if isinstance(parsed,dict) else {}
   if info.get('destination')==wallet and info.get('source'):
    amount=info.get('lamports') or info.get('amount')
    try: amount=int(amount)
    except (TypeError,ValueError): continue
    found.append({'source_wallet':info['source'],'destination_wallet':wallet,'lamports':amount,'mechanism':z.get('program','UNKNOWN'),'instruction_type':parsed.get('type')})
 walk(tx.get('transaction',{}).get('message',{}).get('instructions'))
 for group in tx.get('meta',{}).get('innerInstructions') or []: walk(group.get('instructions'))
 return found
def main():
 if OUT.exists(): raise SystemExit('isolated namespace already exists; refusing reuse')
 OUT.mkdir(); code=sha(__file__); now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
 c=next(x for x in json.loads(BASE.read_text())['candidates'] if x['candidate_id']=='p3r-candidate-13a04d7da7a1fc55'); mints=c['mints']
 con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True); q=','.join('?' for _ in mints)
 rows=con.execute(f"SELECT mint,hop_depth,candidate_parent,wallet,amount_lamports,mechanism,signature,block_time,evidence_key FROM wt_walkback_edge_candidates WHERE mint IN ({q}) AND selection_status='SELECTED' AND hop_depth=4 AND amount_lamports=? ORDER BY mint",(*mints,TARGET)).fetchall(); con.close()
 if len(rows)!=5: raise SystemExit(f'expected exactly 5 target boundary rows, found {len(rows)}')
 starts=[{'mint':r[0],'current_boundary_hop':r[1],'boundary_source_wallet':r[2],'boundary_destination_wallet':r[3],'amount_lamports':r[4],'mechanism':r[5],'boundary_signature':r[6],'block_time':r[7],'evidence_key':r[8]} for r in rows]
 bindings={'candidate_id':'p3r-candidate-13a04d7da7a1fc55','member_mints':mints,'tier1_membership_sha256':sha(TIER),'membership_sha256':sha(BASE),'source_database':str(DB),'source_database_read_only':True,'analysis_code_sha256':code,'acquired_at_utc':now,'provider_ceiling':CEIL,'near_amount_tolerance_lamports':NEAR}
 start=dump(OUT/'p3r_13a04_rpc_start_manifest.v1.json',{'bindings':bindings,'start_boundary_rows':starts,'hypothesis':'Verify whether pre-boundary ancestry exhibits repeated approximately 29.99 SOL funding; not assumed true.'})
 url,provider=envurl(); requests=[]; edges=[]; branches=[]; started=time.monotonic(); total=0
 def rpc(method,params,wallet):
  nonlocal total
  if total>=CEIL['max_total_requests'] or time.monotonic()-started>CEIL['max_elapsed_seconds']: raise RuntimeError('GLOBAL_CEILING_REACHED')
  total+=1
  body=json.dumps({'jsonrpc':'2.0','id':total,'method':method,'params':params}).encode()
  try:
   with urllib.request.urlopen(urllib.request.Request(url,body,{'Content-Type':'application/json'}),timeout=20) as r: ans=json.loads(r.read())
   requests.append({'ordinal':total,'wallet':wallet,'method':method,'status':'OK'}); return ans.get('result')
  except Exception as e:
   requests.append({'ordinal':total,'wallet':wallet,'method':method,'status':'ERROR','error_type':type(e).__name__}); return None
 if not url:
  acquisition={'bindings':bindings,'provider_identity':provider,'provider_requests':requests,'branches_completed':0,'branches_bounded_incomplete':5,'status':'RPC_NOT_CONFIGURED'}
  outputs={'start_manifest':start,'acquisition_manifest':dump(OUT/'p3r_13a04_rpc_acquisition_manifest.v1.json',acquisition)}; print(json.dumps({'verdict':'P3R_13A04_RPC_HOLD','outputs':outputs})); return
 for s in starts:
  wallet=s['boundary_source_wallet']; before=s['boundary_signature']; branch={'mint':s['mint'],'start_wallet':wallet,'status':'COMPLETE','edges':0}
  for depth in range(1,CEIL['max_additional_depth']+1):
   sigs=rpc('getSignaturesForAddress',[wallet,{'limit':CEIL['max_transactions_per_wallet'],'before':before}],wallet)
   if sigs is None: branch['status']='BOUNDED_INCOMPLETE'; break
   selected=None
   for item in sigs[:CEIL['max_transactions_per_wallet']]:
    tx=rpc('getTransaction',[item['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}],wallet)
    if not tx: continue
    candidates=tx_transfers(tx,wallet)
    if candidates:
     selected=max(candidates,key=lambda x:x['lamports']); selected.update({'mint':s['mint'],'relative_hop':depth,'signature':item['signature'],'block_time':tx.get('blockTime'),'transaction_success':tx.get('meta',{}).get('err') is None,'provider_provenance':provider,'classification':'EXACT_REPEATED_AMOUNT' if selected['lamports']==TARGET else 'NEAR_REPEATED_AMOUNT' if abs(selected['lamports']-TARGET)<=NEAR else 'DIFFERENT_AMOUNT'})
     edges.append(selected); branch['edges']+=1; wallet=selected['source_wallet']; before=item['signature']; break
   if not selected: branch['status']='BOUNDED_INCOMPLETE'; break
  branches.append(branch)
 bysource=defaultdict(list)
 for e in edges: bysource[e['source_wallet']].append(e)
 shared=[{'wallet':w,'branches':len({e['mint'] for e in es}),'mints':sorted({e['mint'] for e in es}),'min_hop':min(e['relative_hop'] for e in es),'max_hop':max(e['relative_hop'] for e in es),'signatures':[e['signature'] for e in es],'amount_lamports':sorted({e['lamports'] for e in es})} for w,es in bysource.items() if len({e['mint'] for e in es})>1]
 amount_counts=Counter(e['lamports'] for e in edges); mech=Counter(e['mechanism'] for e in edges)
 convergence='STRONG_SHARED_UPSTREAM_ADDRESS' if shared else 'NO_SHARED_UPSTREAM_ADDRESS' if all(b['status']=='COMPLETE' for b in branches) else 'INSUFFICIENT_RPC_EVIDENCE'
 behavioural='STRONG_UPSTREAM_BEHAVIOURAL_RECURRENCE' if any(v>=3 for v in amount_counts.values()) else 'MODERATE_UPSTREAM_BEHAVIOURAL_RECURRENCE' if any(v>=2 for v in amount_counts.values()) else 'NO_UPSTREAM_BEHAVIOURAL_RECURRENCE' if edges else 'INSUFFICIENT_EVIDENCE'
 edgepath=OUT/'p3r_13a04_upstream_rpc_edges.v1.jsonl'; edgepath.write_text(''.join(json.dumps(e,sort_keys=True)+'\n' for e in edges))
 outputs={'start_manifest':start,'boundary_amount_verification':dump(OUT/'p3r_13a04_boundary_amount_verification.v1.json',{'bindings':bindings,'boundary_edges':[dict(x,classification='EXACT_REPEATED_AMOUNT') for x in starts],'result':'Five retained boundary edges exactly equal 29,999,990,000 lamports; upstream RPC verification is separately represented in acquired edges.'}),'edges':{'path':str(edgepath),'sha256':sha(edgepath)},'branch_graph':dump(OUT/'p3r_13a04_upstream_branch_graph.v1.json',{'bindings':bindings,'branches':branches,'edges':edges}),'convergence':dump(OUT/'p3r_13a04_upstream_convergence.v1.json',{'bindings':bindings,'classification':convergence,'shared_wallets':shared}),'behaviour':dump(OUT/'p3r_13a04_upstream_behavioural_recurrence.v1.json',{'bindings':bindings,'classification':behavioural,'amount_counts':amount_counts,'mechanism_counts':mech,'timing_note':'Only qualified provider block times in edge rows; no lifecycle timing inferred.'}),'signals':dump(OUT/'p3r_13a04_upstream_signal_candidates.v1.json',{'bindings':bindings,'signals':[{'signal':'Repeated exact retained 29,999,990,000-lamport boundary amount','state':'ADDRESS_INDEPENDENT','status':'OBSERVED_RETAINED_BOUNDARY_NOT_YET_UPSTREAM_RPC_CONFIRMED'},{'signal':'Shared upstream wallet','state':'ADDRESS_DEPENDENT','status':'OBSERVED' if shared else 'NOT_OBSERVED'}]}),'acquisition_manifest':dump(OUT/'p3r_13a04_rpc_acquisition_manifest.v1.json',{'bindings':bindings,'provider_identity':provider,'provider_requests':requests,'request_count':total,'branches':branches,'elapsed_seconds':time.monotonic()-started,'ceilings_enforced':True})}
 verdict='P3R_13A04_UPSTREAM_CONVERGENCE_FOUND' if shared else 'P3R_13A04_UPSTREAM_BEHAVIOURAL_RECURRENCE_FOUND' if behavioural=='STRONG_UPSTREAM_BEHAVIOURAL_RECURRENCE' else 'P3R_13A04_UPSTREAM_PARTIAL' if edges else 'P3R_13A04_RPC_HOLD'
 dump(OUT/'p3r_13a04_rpc_artifact_manifest.v1.json',{'bindings':bindings,'artifacts':outputs,'verdict':verdict})
 print(json.dumps({'verdict':verdict,'provider_requests':total,'branches':branches,'shared':shared,'amount_counts':amount_counts,'outputs':outputs},indent=2,default=list))
if __name__=='__main__': main()
