"""Offline deterministic final-v2 harness; no provider I/O."""
import base58
from tempfile import TemporaryDirectory
from .early_buyer_bundle_history import digest, normalize_v2, offline_execute_v2, OfflineStore, repeated_buyer_index
def _pk(n): return base58.b58encode(bytes([n])*32).decode()
def _sig(n): return base58.b58encode(bytes([n])*64).decode()
def population():
    cases=[]
    for i in range(12):
        mint,creator,buyer=_pk(20+i),_pk(2),_pk(3 if i in (0,5,9) else 40+i)
        trades=[{'signature':_sig(80+i),'buyer':buyer,'slot':1,'transaction_index':1,'event_index':0,'block_time':1}]
        if i==1: trades.insert(0,{'signature':_sig(110+i),'buyer':creator,'slot':1,'transaction_index':0,'event_index':0,'block_time':0})
        if i==2: trades=[{'signature':_sig(120+i+j),'buyer':buyer,'slot':1,'transaction_index':j,'event_index':0,'block_time':j} for j in range(21)]
        if i==3: trades.append({'signature':_sig(140+i),'buyer':buyer,'slot':1,'transaction_index':2,'event_index':0,'block_time':61})
        rel=(({ 'members':[buyer,_pk(90+i)],'features':['shared_signer']},) if i==4 else (({'members':[buyer,_pk(90+i)],'qualifying_rule':True},) if i==5 else ()))
        cases.append({'case_id':f'case_{i:02d}','mint':mint,'creator':creator,'trades':trades,'relationships':rel})
    return cases
def manifest(cases=None):
    xs=cases or population();m={'stage':'EARLY_BUYER_BUNDLE_HISTORY_V1','version':'V2','cases':[{'case_id':x['case_id'],'mint':x['mint'],'creator':x['creator']} for x in xs]};m['sha256']=digest(m);return m
def run_once(cases=None):
    xs=cases or population()
    with TemporaryDirectory() as d:
        store=OfflineStore(d);records=[]
        for x in xs:
            ctx={'operation_id':'synthetic','mint':x['mint'],'creator':x['creator'],'launch':{'block_time':0}}
            n=normalize_v2(x['trades'],x['creator'],0,mint=x['mint'])
            r,_=offline_execute_v2(ctx,n,store,x['relationships']);records.append(r)
        return {'manifest':manifest(xs),'records':records,'repeated_buyers':repeated_buyer_index(records),'provider_calls':0}
