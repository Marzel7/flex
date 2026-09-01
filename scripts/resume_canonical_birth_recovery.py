#!/usr/bin/env python3
"""Resume (never initialise) the authorised canonical birth resolver."""
import json, os, re, sys, urllib.request, tempfile, fcntl
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'docs/audits'
LEDGER=AUDIT/'canonical_birth_recovery_run_ledger.v1.json'
RESULTS=AUDIT/'canonical_birth_recovery_results.v1.json'
TXCACHE=AUDIT/'canonical_birth_transaction_cache.v1.jsonl'
SIGCACHE=AUDIT/'canonical_birth_signature_pages.v1.jsonl'
FIRST=AUDIT/'canonical_birth_recovery_first_response.json'
RUN='canonical-birth-4d1c3142547b4896'
QV='QVtWcAX3R7Cr51VhAxFSYntoCAmTQzK8Hf4R1TrKNQ4'

def atomic(path, obj):
    # Same-directory, unique temp file avoids collisions between writers.
    fd, name=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.pending')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(obj,f,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    except BaseException:
        try: os.unlink(name)
        except FileNotFoundError: pass
        raise

def append_fsync(path, record):
    """Durable raw RPC evidence, deliberately written before any classification."""
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record,sort_keys=True,separators=(',',':'))+'\n'); f.flush(); os.fsync(f.fileno())

def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def inputs():
    rows=[json.loads(x) for x in (AUDIT/'potential_operations_6437_funder_creator_edges.v1.jsonl').read_text().splitlines() if x]
    strict=[r for r in rows if r['state']=='PROVEN_ASSOCIATED_CREATOR_10K']
    qvtw=[r for r in rows if r['funder']==QV]
    # QVtW records are variants, not strict-core records.
    assert len(strict)==84 and len(qvtw)==6 and not set(r['mint'] for r in strict)&set(r['mint'] for r in qvtw)
    return [('QVtW',r['mint']) for r in qvtw]+[('STRICT_CORE',r['mint']) for r in strict]

def endpoint():
    t=(ROOT/'config/supervisor/supervisord.conf').read_text()
    return re.search(r'HELIUS_RPC_URL\s*=\s*["\']?([^"\'\n,]+)',t).group(1)

def preaccount(ledger, mint, method, detail):
    lock=LEDGER.with_suffix(LEDGER.suffix+'.lock')
    with lock.open('a+') as lf:
        fcntl.flock(lf,fcntl.LOCK_EX)
        current=load_json(LEDGER,{})
        if current['calls_remaining'] <= 0: raise RuntimeError('CALL_BUDGET_EXHAUSTED')
        entry={'ordinal':current['calls_used']+1,'mint':mint,'method':method,'detail':detail,
               'preaccounted_at':datetime.now(timezone.utc).isoformat(),'outcome':'PENDING'}
        current.setdefault('calls',[]).append(entry)
        current['calls_used']+=1; current['calls_remaining']-=1
        atomic(LEDGER,current) # durable before network I/O
        ledger.clear(); ledger.update(current)
    return entry

def rpc(ledger, url, mint, method, params, detail):
    entry=preaccount(ledger,mint,method,detail)
    try:
        req=urllib.request.Request(url,data=json.dumps({'jsonrpc':'2.0','id':entry['ordinal'],'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=45) as h: body=json.loads(h.read())
        entry['outcome']='OK' if 'error' not in body else 'RPC_ERROR'; entry['response_error']=body.get('error')
    except Exception as e:
        body=None; entry['outcome']='TRANSPORT_ERROR'; entry['error_type']=type(e).__name__; entry['error']=str(e)[:300]
    entry['completed_at']=datetime.now(timezone.utc).isoformat(); atomic(LEDGER,ledger)
    if method=='getTransaction':
        # Preserve the entire provider body (including every instruction form)
        # before this function can return it to a classifier.
        result=(body or {}).get('result') if body else None
        append_fsync(TXCACHE,{'run_id':RUN,'signature':params[0],'mints_under_evaluation':[mint],
            'fetched_at':entry['completed_at'],'cumulative_calls_used':ledger['calls_used'],
            'provider_response':body,'provider_result':result,'provider_error':(body or {}).get('error') if body else entry.get('error'),
            'slot':result.get('slot') if isinstance(result,dict) else None,'blockTime':result.get('blockTime') if isinstance(result,dict) else None,
            'transaction_version':result.get('version') if isinstance(result,dict) else None})
    if method=='getSignaturesForAddress':
        rows=(body or {}).get('result') if body else None
        append_fsync(SIGCACHE,{'run_id':RUN,'mint':mint,'before_cursor':(params[1].get('before') if len(params)>1 else None),
            'signatures':rows,'page_size':len(rows) if isinstance(rows,list) else None,
            'oldest_cursor':rows[-1].get('signature') if isinstance(rows,list) and rows else None,
            'fetched_at':entry['completed_at'],'cumulative_calls_used':ledger['calls_used'],'provider_error':(body or {}).get('error') if body else entry.get('error')})
    return body

def creates_mint(tx,mint):
    if not tx or tx.get('meta',{}).get('err') is not None: return False
    msg=tx.get('transaction',{}).get('message',{})
    keys=msg.get('accountKeys',[])
    if not any((x.get('pubkey') if isinstance(x,dict) else x)==mint for x in keys): return False
    instructions=list(msg.get('instructions',[]))
    for group in tx.get('meta',{}).get('innerInstructions') or []:
        instructions.extend(group.get('instructions',[]))
    for ix in instructions:
        # jsonParsed may still contain partially-decoded / compiled inner
        # instructions.  `parsed` is therefore not guaranteed to be a map.
        if not isinstance(ix,dict):
            continue
        p=ix.get('parsed')
        if not isinstance(p,dict):
            continue
        info=p.get('info')
        if not isinstance(info,dict):
            continue
        if p.get('type') in {'initializeMint','initializeMint2'} and info.get('mint')==mint: return True
    logs=tx.get('meta',{}).get('logMessages') or []
    return any('InitializeMint' in x for x in logs) and mint in json.dumps(tx)

def main():
    ledger=load_json(LEDGER,{})
    assert ledger.get('run_id')==RUN and ledger.get('calls_used')>=1 and ledger.get('calls_remaining')==ledger.get('authorized_calls')-ledger.get('calls_used')
    out=load_json(RESULTS,{'run_id':RUN,'results':{}}); assert out.get('run_id')==RUN
    failed_history={x['mint'] for x in ledger.get('calls',[]) if x.get('method')=='getSignaturesForAddress' and x.get('outcome')=='TRANSPORT_ERROR'}
    for mint in failed_history:
        if out['results'].get(mint,{}).get('status')=='TERMINAL_NO_SIGNATURE_HISTORY':
            out['results'][mint]={'cohort':out['results'][mint]['cohort'],'status':'TERMINAL_RUNTIME_FAILURE'}
    atomic(RESULTS,out)
    url=endpoint(); first=load_json(FIRST,{})
    for cohort,mint in inputs():
        # Preserve prior terminal evidence.  Only a recorded runtime failure is
        # eligible for a resumed attempt; it is never silently reclassified.
        prior=out['results'].get(mint)
        if prior and prior.get('status') not in {'TERMINAL_RUNTIME_FAILURE','TERMINAL_PLAUSIBLE_SIGNATURE_NOT_TOKEN_CREATION'}:
            continue
        sigs=None
        known=prior.get('plausible_signature') if prior else None
        # Rejected candidates require an older page for every cohort; never
        # reacquire the already cached candidate transaction.
        if sigs is not None: pass
        elif first.get('mint')==mint: sigs=first.get('result',[])
        else:
            opts={'limit':1000}
            if known: opts['before']=known
            b=rpc(ledger,url,mint,'getSignaturesForAddress',[mint,opts],'signature history older page')
            sigs=(b or {}).get('result') if b else None
        if sigs is None:
            out['results'][mint]={'cohort':cohort,'status':'TERMINAL_RUNTIME_FAILURE'}; atomic(RESULTS,out); continue
        if not sigs:
            out['results'][mint]={'cohort':cohort,'status':'TERMINAL_NO_SIGNATURE_HISTORY'}; atomic(RESULTS,out); continue
        # A mint cannot appear before its creation; oldest returned success is the sole plausible birth candidate.
        plausible=next((x for x in reversed(sigs) if not x.get('err')),None)
        if not plausible:
            out['results'][mint]={'cohort':cohort,'status':'TERMINAL_NO_SUCCESSFUL_SIGNATURE'}; atomic(RESULTS,out); continue
        b=rpc(ledger,url,mint,'getTransaction',[plausible['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0}],'verify plausible creation signature')
        tx=(b or {}).get('result')
        if b is None:
            out['results'][mint]={'cohort':cohort,'status':'TERMINAL_RUNTIME_FAILURE'}
        elif creates_mint(tx,mint):
            out['results'][mint]={'cohort':cohort,'status':'VERIFIED_CREATE','create_signature':plausible['signature'],'slot':tx.get('slot'),'blockTime':tx.get('blockTime')}
        else:
            out['results'][mint]={'cohort':cohort,'status':'TERMINAL_PLAUSIBLE_SIGNATURE_NOT_TOKEN_CREATION','plausible_signature':plausible['signature'],'slot':plausible.get('slot'),'blockTime':plausible.get('blockTime')}
        atomic(RESULTS,out)
        print(ledger['calls_used'],cohort,mint,out['results'][mint]['status'],flush=True)
        if ledger['calls_remaining']<=0: break
    out['completed_at']=datetime.now(timezone.utc).isoformat(); out['calls_used']=ledger['calls_used']; out['calls_remaining']=ledger['calls_remaining']; atomic(RESULTS,out)
    print(json.dumps({'terminal_results':len(out['results']),'calls_used':ledger['calls_used'],'calls_remaining':ledger['calls_remaining']}))
if __name__=='__main__': main()
