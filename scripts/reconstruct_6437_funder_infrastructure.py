#!/usr/bin/env python3
"""Read-only, resumable 6437 current-funder infrastructure reconstruction.

The cohort is derived solely from retained queue/selected-edge data and the
qualified incremental matcher.  RPC responses are decoded in memory; the
durable cache contains compact decoded observations only, never raw payloads.
"""
from __future__ import annotations

import argparse, collections, hashlib, json, os, re, sqlite3, sys, time, urllib.request, tempfile, fcntl
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.ops.rpc_acquisition_checkpoint import RunLedger,DurableAuthorizationLedger,require_resolved_cache_provenance
DB=ROOT/'database/wt_ops_v2.db'
OUT=ROOT/'docs/audits/potential_operations_6437_full_funder_infrastructure.v2.json'
EVIDENCE=ROOT/'docs/audits/potential_operations_6437_funder_infrastructure_evidence.v2.jsonl'
CACHE=ROOT/'docs/audits/potential_operations_6437_funder_infrastructure_cache.v1.json'
AUTH_LEDGER=ROOT/'docs/audits/potential_operations_6437_f1_rpc_authorization.v1.json'
TARGET='p3r-v2-6437acd385e566e301a7'; USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def pk(x): return x if isinstance(x,str) else (x or {}).get('pubkey')
def url():
    m=re.search(r'^(?:export\s+)?HELIUS_RPC_URL=["\']?([^"\'\n]+)',(ROOT/'.env').read_text(),re.M)
    if not m: raise RuntimeError('HELIUS_RPC_URL_NOT_CONFIGURED')
    return m.group(1)

def signature(c,mint):
    return tuple(c.execute("SELECT hop_depth,mechanism,amount_lamports FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' AND amount_lamports IS NOT NULL ORDER BY hop_depth,signature",(mint,)).fetchall()) or None

def cohort():
    from src.ops.live_potential_activity import MEMBERSHIP,SNAPSHOT
    from src.ops.potential_candidate_matcher import PotentialCandidateMatchSpec,match_signature
    con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True); con.execute('PRAGMA query_only=ON'); c=con.cursor()
    snaps={x['candidate_id'] for x in json.loads(SNAPSHOT.read_text())}; families=[x for x in json.loads(MEMBERSHIP.read_text())['families'] if x['candidate_id'] in snaps]
    families={x['candidate_id']:x for x in families}; values={k:[signature(c,m) for m in x['mints']] for k,x in families.items()}
    qualified={k:v[0] for k,v in values.items() if v and all(v) and len(set(v))==1}; cnt=collections.Counter(qualified.values())
    specs=tuple(PotentialCandidateMatchSpec(k,v) for k,v in qualified.items() if cnt[v]==1)
    rows=[]
    queue_rows=c.execute('SELECT mint,funder_wallet,creator,funder_block_time,funder_sig FROM wt_walkback_queue WHERE funder_wallet IS NOT NULL AND funder_block_time IS NOT NULL').fetchall()
    for mint,funder,creator,bt,fsig in queue_rows:
        hit=match_signature(signature(c,mint),specs)
        if hit.state=='UNIQUE_MATCH' and hit.candidate_ids[0]==TARGET:
            rows.append({'mint':mint,'funder':funder,'creator':creator,'launch_time':bt,'launch_signature':fsig,'route_signature':[list(x) for x in signature(c,mint)]})
    con.close(); return sorted(rows,key=lambda x:(x['funder'],x['launch_time']))

def rpc(endpoint,method,params,state):
    """The sole provider boundary; every physical call is ledgered."""
    ledger=state['ledger']
    req=urllib.request.Request(endpoint,data=json.dumps({'jsonrpc':'2.0','id':state['calls']['total']+1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
    def invoke():
        with urllib.request.urlopen(req,timeout=40) as r: return json.loads(r.read())
    subject=str(params[0]) if params else None
    def dispatched(): return state['authorization'].call('helius',method,subject,invoke,{'cache_path':str(CACHE)})
    body=ledger.call(method,subject,dispatched)
    state['calls']['total']=ledger.network_calls; state['calls'][method]=sum(x['method']==method for x in ledger.entries)
    if body.get('error'): raise RuntimeError(f'{method}:{body["error"]}')
    return body.get('result')

def parsed_instructions(tx):
    msg=tx.get('transaction',{}).get('message',{}); result=list(msg.get('instructions') or [])
    for group in (tx.get('meta') or {}).get('innerInstructions') or []: result.extend(group.get('instructions') or [])
    return [x for x in result if isinstance(x,dict) and isinstance(x.get('parsed'),dict)]

def decode(tx, watch):
    if not tx: return None
    msg=tx.get('transaction',{}).get('message',{}); keys=[pk(x) for x in msg.get('accountKeys') or []]; meta=tx.get('meta') or {}; pre=meta.get('preBalances') or []; post=meta.get('postBalances') or []
    owners={}
    for b in (meta.get('preTokenBalances') or [])+(meta.get('postTokenBalances') or []):
        if b.get('owner') and b.get('accountIndex') is not None and b['accountIndex']<len(keys): owners[keys[b['accountIndex']]]=b['owner']
    native=[]; transfers=[]
    for i,(a,b) in enumerate(zip(pre,post)):
        if i<len(keys) and b-a>0 and keys[i] in watch:
            src=next((keys[j] for j,(x,y) in enumerate(zip(pre,post)) if x-y>=b-a and j!=i),None)
            native.append({'source':src,'destination':keys[i],'lamports':b-a,'sol':(b-a)/1e9})
    for ix in parsed_instructions(tx):
        p=ix['parsed']; typ=p.get('type'); info=p.get('info') or {}
        if typ not in ('transfer','transferChecked'): continue
        dest=info.get('destination'); src=info.get('source'); authority=info.get('authority') or info.get('multisigAuthority')
        amount=info.get('amount') or (info.get('tokenAmount') or {}).get('amount'); decimals=(info.get('tokenAmount') or {}).get('decimals')
        mint=info.get('mint')
        if amount is None: continue
        try: amount=int(amount)
        except (ValueError,TypeError): continue
        transfers.append({'type':typ,'source_token_account':src,'source_owner':owners.get(src),'destination_token_account':dest,'destination_owner':owners.get(dest),'authority':authority,'mint':mint,'raw_amount':amount,'decimals':decimals,'normalized_amount':amount/(10**decimals) if decimals is not None else None})
    return {'signature':tx.get('transaction',{}).get('signatures',[None])[0],'block_time':tx.get('blockTime'),'fee_payer':keys[0] if keys else None,'signers':[pk(x) for x in msg.get('accountKeys') or [] if isinstance(x,dict) and x.get('signer')],'native_inbound':native,'token_transfers':transfers}

def windows(rows):
    by=collections.defaultdict(list)
    for r in rows: by[r['funder']].append((r['launch_time']-7*86400,r['launch_time']+86400))
    out={}
    for f,vals in by.items():
        vals.sort(); merged=[]
        for a,b in vals:
            if merged and a<=merged[-1][1]: merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
            else: merged.append((a,b))
        out[f]=merged
    return out

def relevant(t,ws): return t is not None and any(a<=t<=b for a,b in ws)
def target_signatures(history, launches):
    chosen=[]
    for launch in launches:
        nearby=[x for x in history if x.get('blockTime') and 0 <= launch['launch_time']-x['blockTime'] <= 86400]
        if not nearby:
            nearby=[x for x in history if x.get('blockTime') and 0 <= launch['launch_time']-x['blockTime'] <= 7*86400]
        chosen.extend(sorted(nearby,key=lambda x:launch['launch_time']-x['blockTime'])[:12])
    return {x['signature'] for x in chosen if x.get('signature')}
HISTORY_NOT_STARTED='NOT_STARTED'; HISTORY_PARTIAL='PARTIAL'; HISTORY_COMPLETE='COMPLETE'
DECODED='DECODED'; FAILED_TERMINAL='FAILED_TERMINAL'; FAILED_RETRYABLE='FAILED_RETRYABLE'; NOT_DECODED='NOT_DECODED'

def load_cache(path=CACHE):
    if path.exists():
        try:
            c=json.loads(path.read_text()); c['_checkpoint_base_revision']=c.get('checkpoint_revision',0); return c
        except json.JSONDecodeError: pass
    return {'schema_version':'6437_compact_decoded_cache.v2','checkpoint_revision':0,'_checkpoint_base_revision':0,'transactions':{},'transaction_status':{},'histories':{},'calls':{'total':0,'getSignaturesForAddress':0,'getTransaction':0}}
def save_cache(c, path=CACHE):
    """Locked CAS plus fsynced same-directory unique temporary replacement."""
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_name(path.name+'.checkpoint.lock')
    with open(lock,'a+') as lf:
      fcntl.flock(lf,fcntl.LOCK_EX)
      current=0
      if path.exists(): current=json.loads(path.read_text()).get('checkpoint_revision',0)
      expected=c.get('_checkpoint_base_revision',current)
      if current!=expected: raise RuntimeError('CHECKPOINT_REVISION_CONFLICT')
      c['checkpoint_revision']=current+1; c['_checkpoint_base_revision']=current+1
      fd,name=tempfile.mkstemp(prefix=path.name+'.checkpoint.',suffix='.tmp',dir=path.parent)
      try:
        with os.fdopen(fd,'w') as f:
          f.write(json.dumps(c,sort_keys=True,indent=2)+'\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
        try:
          dfd=os.open(path.parent,os.O_RDONLY); os.fsync(dfd); os.close(dfd)
        except OSError: pass
      finally:
        if os.path.exists(name): os.unlink(name)
      fcntl.flock(lf,fcntl.LOCK_UN)

def _history(funder, window, now):
    return {'funder':funder,'state':HISTORY_NOT_STARTED,'page_count':0,
            'signatures_retained':[], 'newest_signature':None,'oldest_signature':None,
            'next_before_cursor':None,'launch_window_start':min(a for a,b in window),
            'launch_window_end':max(b for a,b in window),'oldest_block_time_reached':None,
            'history_exhausted':False,'required_window_reached':False,
            'last_successful_page':0,'updated_at':now}

class FunderAcquirer:
    """Actual runner orchestration: page checkpoints precede every next call."""
    def __init__(self, cache, ledger, call, persist=save_cache, now=lambda: int(time.time())):
        self.cache, self.ledger, self.call, self.persist, self.now = cache,ledger,call,persist,now
        self.cache.setdefault('histories',{}); self.cache.setdefault('transactions',{}); self.cache.setdefault('transaction_status',{})
    def _save(self):
        self.cache['calls']={'total':self.ledger.network_calls,
          'getSignaturesForAddress':sum(x['method']=='getSignaturesForAddress' for x in self.ledger.entries),
          'getTransaction':sum(x['method']=='getTransaction' for x in self.ledger.entries)}
        self.persist(self.cache)
    def acquire_history(self, funder, window):
        h=self.cache['histories'].setdefault(funder,_history(funder,window,self.now()))
        # Migrate the legacy list conservatively using the same canonical boundary,
        # rather than treating the mere presence of signatures as complete.
        if isinstance(h,list):
            old=h; h=_history(funder,window,self.now()); h['signatures_retained']=old
            h['page_count']=1 if old else 0; h['last_successful_page']=h['page_count']
            sigs=[x.get('signature') for x in old if x.get('signature')]
            h['newest_signature']=sigs[0] if sigs else None; h['oldest_signature']=sigs[-1] if sigs else None; h['next_before_cursor']=h['oldest_signature']
            times=[x.get('blockTime') for x in old if x.get('blockTime') is not None]
            h['oldest_block_time_reached']=min(times) if times else None
            h['required_window_reached']=h['oldest_block_time_reached'] is not None and h['oldest_block_time_reached']<=h['launch_window_start']
            h['history_exhausted']=not old
            h['state']=HISTORY_COMPLETE if h['required_window_reached'] or h['history_exhausted'] else HISTORY_PARTIAL
            self.cache['histories'][funder]=h; self._save()
        if h['state']==HISTORY_COMPLETE: return h
        while h['state'] != HISTORY_COMPLETE:
            before=h['next_before_cursor']; page=self.call('getSignaturesForAddress',[funder,{'limit':1000,'commitment':'confirmed',**({'before':before} if before else {})}])
            if not isinstance(page,list): raise RuntimeError('INVALID_SIGNATURE_PAGE')
            if not page:
                h.update(history_exhausted=True,state=HISTORY_COMPLETE,updated_at=self.now()); self._save(); break
            sigs=[x.get('signature') for x in page if isinstance(x,dict) and x.get('signature')]
            if not sigs: raise RuntimeError('INVALID_SIGNATURE_PAGE')
            next_cursor=sigs[-1]
            known={x.get('signature') for x in h['signatures_retained']}
            new_rows=[x for x in page if x.get('signature') not in known]
            if (before and next_cursor==before) or not new_rows:
                h.update(state=HISTORY_PARTIAL,terminal_state='PAGINATION_NO_PROGRESS',updated_at=self.now()); self._save(); raise RuntimeError('PAGINATION_NO_PROGRESS')
            h['signatures_retained'].extend(new_rows)
            h['page_count']+=1; h['last_successful_page']=h['page_count']; h['newest_signature']=h['signatures_retained'][0]['signature']
            h['oldest_signature']=next_cursor; h['next_before_cursor']=next_cursor
            times=[x.get('blockTime') for x in h['signatures_retained'] if x.get('blockTime') is not None]
            h['oldest_block_time_reached']=min(times) if times else None
            h['required_window_reached']=h['oldest_block_time_reached'] is not None and h['oldest_block_time_reached']<=h['launch_window_start']
            h['state']=HISTORY_COMPLETE if h['required_window_reached'] else HISTORY_PARTIAL
            h['updated_at']=self.now(); self._save() # atomic page + cursor + state
        return h
    def decode(self, funder, launches):
        h=self.cache['histories'][funder]; wanted=target_signatures(h['signatures_retained'],launches)
        for sig in sorted(wanted):
            status=self.cache['transaction_status'].get(sig,NOT_DECODED)
            if status in (DECODED,FAILED_TERMINAL): continue
            try:
                tx=self.call('getTransaction',[sig,{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}])
                self.cache['transactions'][sig]=decode(tx,{funder}) or {'signature':sig,'null':True}
                self.cache['transaction_status'][sig]=DECODED
            except RuntimeError as exc:
                if str(exc)=='RPC_BUDGET_EXHAUSTED': self._save(); raise
                self.cache['transaction_status'][sig]=FAILED_RETRYABLE; self._save(); raise
            except Exception:
                self.cache['transaction_status'][sig]=FAILED_RETRYABLE; self._save(); raise
            self._save()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--call-ceiling',type=int,default=2500); ap.add_argument('--only-funder'); ap.add_argument('--authorization-ledger',type=Path,default=AUTH_LEDGER); ap.add_argument('--new-run',action='store_true'); ap.add_argument('--resume-run'); ap.add_argument('--purpose',default='6437_F1_COMPLETION'); args=ap.parse_args()
    if args.new_run == bool(args.resume_run): raise RuntimeError('EXPLICIT_NEW_OR_RESUME_RUN_REQUIRED')
    if args.new_run:
        run_id=f'6437-f1-completion-{int(time.time())}'; authorization=DurableAuthorizationLedger.new(args.authorization_ledger,run_id,args.purpose,TARGET,args.call_ceiling)
    else: authorization=DurableAuthorizationLedger.resume(args.authorization_ledger,args.resume_run,args.purpose,TARGET)
    rows=cohort(); ws=windows(rows); cache=load_cache()
    if not args.new_run: require_resolved_cache_provenance(cache,authorization)
    cache.pop('checkpoint_error',None); state={'calls':collections.Counter(),'ledger':RunLedger(authorization.remaining),'authorization':authorization}; endpoint=url()
    acquirer=FunderAcquirer(cache,state['ledger'],lambda method,params: rpc(endpoint,method,params,state))
    launches_by=collections.defaultdict(list)
    for row in rows: launches_by[row['funder']].append(row)
    try:
      for funder, w in ws.items():
        if args.only_funder and funder != args.only_funder: continue
        h=acquirer.acquire_history(funder,w)
        if h['state'] == HISTORY_COMPLETE:
            acquirer.decode(funder,launches_by[funder])
    except Exception as exc:
      cache['checkpoint_error']=str(exc); acquirer._save()
    acquirer._save()
    # Compact relationship extraction.
    evidence=[]; f1=collections.defaultdict(set); fee=collections.defaultdict(set); auth=collections.defaultdict(set); sol=collections.Counter(); usdc=collections.Counter()
    for funder, launches in launches_by.items():
      for tx in cache['transactions'].values():
        t=tx.get('block_time')
        if not relevant(t,ws[funder]): continue
        for n in tx.get('native_inbound',[]):
          if n['destination']!=funder or not n.get('source'): continue
          for l in launches:
            delta=l['launch_time']-t
            if 0<=delta<=7*86400:
              f1[n['source']].add(funder); sol[n['lamports']]+=1; evidence.append({'funder':funder,'upstream':n['source'],'hop_level':1,'asset':'SOL','amount':n['sol'],'transaction_signature':tx['signature'],'provisioning_time':t,'launch_mint':l['mint'],'launch_time':l['launch_time'],'time_to_launch':delta})
        for x in tx.get('token_transfers',[]):
          if x.get('destination_owner')!=funder or not x.get('source_owner'): continue
          for l in launches:
            delta=l['launch_time']-t
            if 0<=delta<=7*86400:
              f1[x['source_owner']].add(funder); auth[x.get('authority')].add(funder); fee[tx.get('fee_payer')].add(funder)
              if x.get('mint')==USDC: usdc[x.get('normalized_amount')]+=1
              evidence.append({'funder':funder,'upstream':x['source_owner'],'hop_level':1,'asset':x.get('mint'),'amount':x.get('normalized_amount'),'raw_amount':x.get('raw_amount'),'transaction_signature':tx['signature'],'provisioning_time':t,'launch_mint':l['mint'],'launch_time':l['launch_time'],'time_to_launch':delta,'authority':x.get('authority'),'fee_payer':tx.get('fee_payer')})
    # Per-funder decode cannot reliably expose transfer records where the funder is only an account key; record limitation rather than invent F2.
    recurrence=collections.Counter(len(v) for v in launches_by.values()); bucket={'1 launch':0,'2 launches':0,'3-5':0,'6-10':0,'>10':0}
    for n in recurrence.elements(): bucket['1 launch' if n==1 else '2 launches' if n==2 else '3-5' if n<=5 else '6-10' if n<=10 else '>10']+=1
    funders=len(launches_by); ranked=sorted(((k,len(v)) for k,v in f1.items()),key=lambda x:(-x[1],x[0])); coverage=sum(len(v) for v in f1.values())
    report={'schema_version':'potential_operations_6437_full_funder_infrastructure.v1','status':'PARTIAL' if cache.get('checkpoint_error') else 'ACQUIRED','analysis_cutoff':int(time.time()),'cohort':{'candidate_id':TARGET,'launch_count':len(rows),'distinct_funders':funders,'distinct_creators':len({r['creator'] for r in rows}),'earliest_launch':min(r['launch_time'] for r in rows),'latest_launch':max(r['launch_time'] for r in rows),'launches':rows},'recurrence_distribution':bucket,'funder_windows':ws,'provider_accounting':dict(state['calls']),'cache_hits':0,'decoded_transactions':len(cache['transactions']),'f1_upstreams':[{'wallet':x,'funders_reached':n,'cohort_share':n/funders} for x,n in ranked],'f2_upstreams':[],'amounts':{'sol_lamports':sol.most_common(20),'usdc':usdc.most_common(20)},'shared_fee_payers':[{'wallet':x,'funders':len(v)} for x,v in fee.items() if x and len(v)>1],'shared_authorities':[{'wallet':x,'funders':len(v)} for x,v in auth.items() if x and len(v)>1],'limitations':['F2 requires targeted histories of recurrent F1 wallets and is deferred until F1 records are actually acquired.','Token-account ownership is derived from transaction token balance metadata when present; absent metadata is not inferred.'],'safety':{'real_db_writes':0,'source_table_writes':0,'assignment_writes':0,'membership_writes':0,'living_publications':0,'ranking_writes':0,'generic_dispatch_changes':0}}
    report['result_digest']=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest(); OUT.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    EVIDENCE.write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in evidence)); print(json.dumps({'status':report['status'],'launches':len(rows),'funders':funders,'decoded':len(cache['transactions']),'calls':dict(state['calls']),'report_sha256':sha(OUT),'evidence_sha256':sha(EVIDENCE),'checkpoint_error':cache.get('checkpoint_error')},sort_keys=True))
if __name__=='__main__': main()
