import ast, copy, json, pathlib, typing, struct, base64, pytest
from src.core import walkback_worker as w
# Import exact installed startup contracts for dependency receipt and compatibility.
from src.core import walkback_queue, treasury_bank
from src.ops import anchor_reconciliation, attribution_outcome, operation_fingerprint_drift
ROOT=pathlib.Path(__file__).resolve().parents[1]
A=ROOT/'docs/audits/dev079'
S='1'*88
@pytest.mark.parametrize('version',['legacy',0,1])
def test_versions_and_request_budget(version):
    tx={'version':version,'transaction':{'signatures':[S]},'meta':{'err':None}}
    calls=[]
    def transport(method,params):calls.append((method,params));return tx
    assert w._get_tx(S,rpc_transport=transport) is tx
    assert calls==[('getTransaction',[S,{'encoding':'jsonParsed','maxSupportedTransactionVersion':1,'commitment':'confirmed'}])]
@pytest.mark.parametrize('response',[None,{}, {'transaction':None},{'transaction':{'signatures':[]}}, {'transaction':{'signatures':['2'*88]}},{'transaction':{'signatures':S}},{'error':{'code':-32015}}])
def test_bad_response_no_retry(response):
    calls=[]
    def transport(*args):calls.append(args);return response
    assert w._get_tx(S,rpc_transport=transport) is None
    assert len(calls)==1

def test_timeout_no_retry():
    calls=[]
    def transport(*args):calls.append(args);raise TimeoutError('fixture')
    with pytest.raises(TimeoutError):w._get_tx(S,rpc_transport=transport)
    assert len(calls)==1

def test_rpc_timeout_error_and_endpoint_unchanged(monkeypatch):
    calls=[]
    def fail(request,timeout):calls.append((request.full_url,timeout));raise TimeoutError('fixture')
    monkeypatch.setattr(w.urllib.request,'urlopen',fail)
    assert w._get_tx(S) is None
    assert calls==[(w.RPC_URL,w.RPC_TIMEOUT)]

def test_version2_error_no_retry():
    calls=[]
    def transport(method,params):calls.append(params);assert params[1]['maxSupportedTransactionVersion']==1;return None
    assert w._get_tx(S,rpc_transport=transport) is None
    assert len(calls)==1

def test_existing_startup_import_contracts():
    for mod in [walkback_queue,treasury_bank,anchor_reconciliation,attribution_outcome,operation_fingerprint_drift]:assert callable(mod.validate_schema)

def decoder():
    ns=dict(vars(typing));ns.update(struct=struct,base64=base64)
    ns.update(SYSTEM_PROGRAM='11111111111111111111111111111111',PUMPFUN_PROGRAM_IDS={'6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'},PUMPFUN_BONDING_CURVE_PROGRAM='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',PUMPFUN_AMM_PROGRAM='pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',TOKEN_PROGRAM='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',TOKEN_2022='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
    exec(compile((A/'installed-strict-decoder.py.txt').read_text(),'<unchanged_installed_strict_decoder>','exec'),ns)
    obj=ns['InstalledStrictDecoder'].__new__(ns['InstalledStrictDecoder']);obj.token_mint='68yywen51ppvR2zWptcpvmc5H5a5SC6tsz8evAP2pump';return obj

def create_fixture():
    d=json.loads((A/'retained-derived-controls.json').read_text())['transactions'][0]
    outer=[];inner={}
    for i in d['instructions']:
        ix={'programId':i['program']}
        if 'type' in i:ix['parsed']={'type':i['type'],'info':i['fields']}
        else:ix['accounts']=i.get('accounts',[])
        if i['scope']=='outer':outer.append(ix)
        else:inner.setdefault(i['parent'],[]).append(ix)
    return {'version':d['version'],'slot':d['slot'],'blockTime':d['block_time'],'transaction':{'signatures':[d['signature']],'message':{'accountKeys':[{'pubkey':k} for k in d['accounts']],'instructions':outer}},'meta':{'err':None,'innerInstructions':[{'index':k,'instructions':v} for k,v in inner.items()]}}

def test_retained_v1_create_v2_token2022_and_key_resolution():
    tx=create_fixture();o=decoder();r=o._validate_pumpfun_create_tx(tx)
    assert r['is_pumpfun_create'] and r['mint_create_found'] and r['mint_init_found']
    assert o._resolve_account_key(tx['transaction']['message'],0)==tx['transaction']['message']['accountKeys'][0]['pubkey']
    assert o._resolve_account_key(tx['transaction']['message'],9999) is None
    got=w._get_tx(tx['transaction']['signatures'][0],rpc_transport=lambda *a:tx);assert got is tx

def test_legacy_SPL_validation():
    tx=create_fixture()
    def replace(v):
        if isinstance(v,dict):return {k:replace(x) for k,x in v.items()}
        if isinstance(v,list):return [replace(x) for x in v]
        return 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA' if v=='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb' else v
    assert decoder()._validate_pumpfun_create_tx(replace(tx))['is_pumpfun_create']
@pytest.mark.parametrize('mutation',['wrong_mint','wrong_owner_program','missing_instruction'])
def test_strict_create_negatives(mutation):
    tx=create_fixture();o=decoder()
    if mutation=='wrong_mint':o.token_mint='WRONG_MINT'
    if mutation=='missing_instruction':tx['meta']['innerInstructions']=[]
    if mutation=='wrong_owner_program':
        for group in tx['meta']['innerInstructions']:
            for ix in group['instructions']:
                p=ix.get('parsed',{});info=p.get('info',{})
                if p.get('type')=='createAccount' and info.get('owner')=='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P':info['owner']='11111111111111111111111111111111'
    assert not o._validate_pumpfun_create_tx(tx)['is_pumpfun_create']

def test_retained_funding_path_unchanged():
    d=json.loads((A/'retained-derived-controls.json').read_text())['transactions'][1]
    keys=d['accounts'];changes={r['account']:r for r in d['balance_changes']};tx={'version':d['version'],'slot':d['slot'],'blockTime':d['block_time'],'transaction':{'signatures':[d['signature']],'message':{'accountKeys':[{'pubkey':k} for k in keys],'instructions':[{'programId':i['program'],'parsed':{'type':i['type'],'info':i['fields']}} for i in d['instructions']]}},'meta':{'preBalances':[changes.get(k,{}).get('pre',0) for k in keys],'postBalances':[changes.get(k,{}).get('post',0) for k in keys]}}
    receiver='7ijDgEvd3zZdX3jFrZRzREd4dcugzkexBxGdtrbYDHSn'
    assert w._extract_sol_sender(tx,receiver)=='Hauxmaft8M8zMHFnwYCJna7ZmJRuHpoPr8kgmSYt2LDL'
    assert w._extract_amount_sol(tx,receiver)==.253039  # Existing worker rounds receipt to six decimals.
    assert w._detect_mechanism(tx,d['fee_payer'],receiver)=='WSOL_WRAP_CLOSE'
    assert w._extract_sol_sender(tx,'ABSENT_WALLET') is None

def test_budget_constants_and_nonpatch_source_unchanged():
    import hashlib
    manifest=json.loads((A/'installed-source-composition.json').read_text())
    for item in manifest['files']:
        assert hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['candidate_sha256']
        if item['path']!='src/core/walkback_worker.py':assert item['candidate_sha256']==item['installed_sha256']
    assert w.RPC_BUDGET_BATCH==80 and w.TX_FETCH_LIMIT>0

def test_existing_batch_budget_exhaustion(monkeypatch):
    import sqlite3
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('create table wt_walkback_queue(mint text,creator text,subprov text,treasury text,walkback_class text,attempts integer,status text,lease_expires_at integer,next_retry_at integer,priority integer,enqueued_at integer,intelligence_outcome text)')
    for i in range(2):conn.execute("insert into wt_walkback_queue values(?,NULL,NULL,NULL,'FULL_WALKBACK',0,'pending',0,0,0,?,NULL)",(str(i),i))
    monkeypatch.setattr(w.deep_walkback,'ensure_schema',lambda *a:None)
    claims=[];processed=[]
    monkeypatch.setattr(w,'_mark_running',lambda ops,mint:claims.append(mint) or True)
    monkeypatch.setattr(w,'_process_row',lambda ops,row:processed.append(row['mint']) or 80)
    monkeypatch.setattr(w,'promote_recurring_funders',lambda *a:0)
    r=w.drain_batch(conn,batch_size=2,rpc_budget=80)
    assert r['processed']==1 and r['rpc_used']==80 and claims==['0'] and processed==['0']
    conn.close()

def test_existing_funding_selection_injected_transport():
    data=json.loads((A/'retained-derived-controls.json').read_text())['transactions'][1]
    keys=data['accounts'];changes={r['account']:r for r in data['balance_changes']}
    tx={'version':1,'slot':data['slot'],'blockTime':data['block_time'],'transaction':{'signatures':[data['signature']],'message':{'accountKeys':[{'pubkey':k} for k in keys],'instructions':[{'programId':i['program'],'parsed':{'type':i['type'],'info':i['fields']}} for i in data['instructions']]}},'meta':{'preBalances':[changes.get(k,{}).get('pre',0) for k in keys],'postBalances':[changes.get(k,{}).get('post',0) for k in keys]}}
    calls=[]
    def transport(method,params):
        calls.append((method,params))
        if method=='getSignaturesForAddress':
            assert params[1]['before']=='QUALIFIED_CREATE_BOUNDARY'
            return [{'signature':data['signature'],'slot':data['slot'],'err':None}]
        if method=='getTransaction':return tx
        if method=='getAccountInfo':return {'value':{'owner':'11111111111111111111111111111111'}}
        raise AssertionError(method)
    counter=[0]
    r=w._find_funder_via_rpc('7ijDgEvd3zZdX3jFrZRzREd4dcugzkexBxGdtrbYDHSn',counter,before_signature='QUALIFIED_CREATE_BOUNDARY',rpc_transport=transport)
    assert r[0]=='Hauxmaft8M8zMHFnwYCJna7ZmJRuHpoPr8kgmSYt2LDL' and r[1]==data['signature'] and r[-1]=='WSOL_WRAP_CLOSE'
    assert counter==[3] and [m for m,p in calls]==['getSignaturesForAddress','getTransaction','getAccountInfo']
