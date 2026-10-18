"""Provider-free CREATE compatibility; retained normalized evidence, no Pump ABI invented."""
import ast
import asyncio
import copy
import importlib
import json
from pathlib import Path
import socket
import sys
import types
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
SYSTEM = '11111111111111111111111111111111'
PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
RETAINED = json.loads((ROOT/'docs/audits/dev051/TX1_decoded.json').read_text())
MINT = RETAINED['instructions'][0]['accounts'][0]
SIG = RETAINED['signature']

@pytest.fixture
def module(monkeypatch):
    # Actual module import, with import-time metrics initialization and dotenv
    # disabled in the test only. Never import/start the operational worker.
    metrics = types.ModuleType('src.metrics.rpc_metrics_recorder')
    metrics.record_request = lambda *a, **kw: None
    metrics.initialize_recorder = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, 'src.metrics.rpc_metrics_recorder', metrics)
    import dotenv
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *a, **kw: False)
    # Avoid urllib3's optional import-time IPv6 bind probe under the frozen
    # zero-network guard. Transport calls remain mocked/denied below.
    monkeypatch.setattr(socket, 'has_ipv6', False)
    mod = importlib.import_module('src.analysis.pump_fun_post_migration_analyzer')
    monkeypatch.setattr(mod, 'RPC_URLS', ['https://offline.invalid'])
    monkeypatch.setattr(mod, 'HISTORY_RPC_URLS', ['https://offline.invalid'])
    monkeypatch.setattr(mod, 'HELIUS_API_KEY', '')
    def denied(*a, **kw): raise AssertionError('Provider forbidden')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    # AF_UNIX asyncio self-pipe is local; Internet socket connects are denied.
    original = socket.socket.connect
    monkeypatch.setattr(socket.socket, 'connect', lambda s,a: denied() if s.family in (socket.AF_INET,socket.AF_INET6) else original(s,a))
    return mod


def retained_shape(version=1):
    outer, inner = [], {}
    for entry in RETAINED['instructions']:
        ix = {'programId': entry['program_id']}
        if 'info' in entry: ix['parsed'] = {'type': entry['type'], 'info': copy.deepcopy(entry['info'])}
        if 'accounts' in entry: ix['accounts'] = list(entry['accounts'])
        if entry['inner_index'] is None: outer.append(ix)
        else: inner.setdefault(entry['outer_index'], []).append(ix)
    keys = list(dict.fromkeys([RETAINED['fee_payer']] + RETAINED['signers'] + [a for i in RETAINED['instructions'] for a in i.get('accounts',[])]))
    return {'version':version, 'slot':RETAINED['slot'], 'blockTime':RETAINED['block_time'],
            'transaction': {'signatures':[SIG], 'message':{'accountKeys':[{'pubkey':k,'signer':k in RETAINED['signers'],'writable':True} for k in keys], 'instructions':outer}},
            'meta':{'err':None,'innerInstructions':[{'index':i,'instructions':v} for i,v in inner.items()]}}


def analyzer(module):
    a = object.__new__(module.PostMigrationAnalyzer)
    a.token_mint = MINT
    for key in ['_create_tx_signature','_create_tx_validation','_create_tx_creator','_oldest_scanned_sig','_oldest_scanned_tx']:
        setattr(a,key,None)
    return a


class Response:
    status = 200
    def __init__(self, body): self.body = body
    async def __aenter__(self): return self
    async def __aexit__(self,*a): return False
    async def json(self): return copy.deepcopy(self.body)


def transport(monkeypatch, module, tx, error=None):
    calls = []
    class Session:
        def __init__(self,*a,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        def post(self,url,json,timeout):
            calls.append((copy.deepcopy(json), timeout.total))
            assert json['params'][1]['maxSupportedTransactionVersion'] == 1
            if error: return Response({'error':{'code':error,'message':'synthetic fixture error'}})
            return Response({'result':tx})
    monkeypatch.setattr(module.aiohttp,'ClientSession',Session)
    return calls, Session


@pytest.mark.parametrize('version',['legacy',0,1])
def test_all_three_retrieval_sites(module,monkeypatch,version):
    tx=retained_shape(version); calls,Session=transport(monkeypatch,module,tx)
    async def history(session,url,payload,timeout_s=30):
        assert payload['method']=='getSignaturesForAddress'
        return {'result':[{'signature':SIG,'blockTime':RETAINED['block_time'],'err':None}]}
    monkeypatch.setattr(module,'_rpc_post',history)
    a=analyzer(module)
    async def run():
        assert await a.fetch_tx_with_retry(Session(),SIG) == tx
        assert await a._get_blocktime_for_signature(SIG) == RETAINED['block_time']
        assert await a.extract_bonding_curve_from_creation_tx(max_pages_override=1)
    asyncio.run(run())
    assert len(calls)==3
    assert [timeout for _,timeout in calls]==[module.RPC_TIMEOUT,15,10]
    assert a._create_tx_signature==SIG
    # This field is an existing heuristic, not a decoded Pump creator argument.
    assert a._create_tx_creator==RETAINED['fee_payer']
    assert all('data' not in ix for ix in tx['transaction']['message']['instructions'])


def test_authoritative_program_ids(module):
    from spl.token.constants import TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID
    assert module.TOKEN_PROGRAM == str(TOKEN_PROGRAM_ID) == TOKEN
    assert module.TOKEN_2022 == str(TOKEN_2022_PROGRAM_ID) == TOKEN2022


@pytest.mark.parametrize('program',[TOKEN,TOKEN2022])
def test_legacy_and_token2022_strict_validation(module,program):
    tx=retained_shape()
    for group in [tx['transaction']['message']['instructions']]+[x['instructions'] for x in tx['meta']['innerInstructions']]:
        for ix in group:
            if ix.get('programId')==TOKEN2022:ix['programId']=program
            info=(ix.get('parsed') or {}).get('info') or {}
            if info.get('owner')==TOKEN2022:info['owner']=program
    result=analyzer(module)._validate_pumpfun_create_tx(tx)
    assert result['mint_create_found'] and result['mint_init_found'] and result['is_pumpfun_create']


@pytest.mark.parametrize('mutation',['wrong_program','wrong_mint','missing_mint','missing_curve','missing_all'])
def test_strict_rejections(module,mutation):
    tx=retained_shape()
    if mutation=='missing_all':
        tx['transaction']['message']['instructions']=[];tx['meta']['innerInstructions']=[]
    for group in [tx['transaction']['message']['instructions']]+[x['instructions'] for x in tx['meta']['innerInstructions']]:
        for ix in group[:]:
            info=(ix.get('parsed') or {}).get('info') or {}
            if mutation=='wrong_program':
                if ix.get('programId')==TOKEN2022:ix['programId']=SYSTEM
                if info.get('owner')==TOKEN2022:info['owner']=SYSTEM
            if mutation in ('wrong_mint','missing_mint'):
                for k in ['newAccount','mint']:
                    if info.get(k)==MINT:info[k]='OtherMintFixture'
            if mutation=='missing_curve' and info.get('owner')==PUMP:group.remove(ix)
    a=analyzer(module)
    if mutation=='wrong_mint': a.token_mint='DifferentExpectedMint'
    assert not a._validate_pumpfun_create_tx(tx)['is_pumpfun_create']


def test_version_error_and_failure_propagation(module,monkeypatch):
    calls,Session=transport(monkeypatch,module,retained_shape(),error=-32015)
    a=analyzer(module)
    async def history(*a,**kw):return {'result':[{'signature':SIG,'err':None}]}
    monkeypatch.setattr(module,'_rpc_post',history)
    async def run():
        assert await a.fetch_tx_with_retry(Session(),SIG) is None
        assert await a._get_blocktime_for_signature(SIG) is None
        assert await a.extract_bonding_curve_from_creation_tx(max_pages_override=1) is None
    asyncio.run(run())
    assert len(calls)==3 and a._create_tx_signature is None


def test_only_five_literals_changed():
    current=(ROOT/'src/analysis/pump_fun_post_migration_analyzer.py').read_text()
    tree=ast.parse(current)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PostMigrationAnalyzer')
    helper=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_transaction_signature_matches')
    lines=current.splitlines(keepends=True)
    del lines[helper.lineno-1:helper.end_lineno+1]
    current=''.join(lines)
    guards=[('                            ', 'result, sig', 'return None'),
            ('                    ', 'tx, create_tx_sig, enhanced=True', 'return None'),
            ('        ', 'tx, sig', 'return None'),
            ('                    ', 'tx, sig', 'continue')]
    for indent,args,action in guards:
        guard=indent+'if not self._transaction_signature_matches('+args+'):\n'+indent+'    '+action+'\n'
        assert current.count(guard)==1
        current=current.replace(guard,'')

    reconstructed=current.replace('"maxSupportedTransactionVersion": 1','"maxSupportedTransactionVersion": 0').replace(TOKEN2022,'TokenzQdBbjFD8aff5ZZUwWWwG6Go5rm5KWQEypdCU8').replace(TOKEN,'TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg')
    import hashlib
    authority=json.loads((ROOT/'docs/audits/dev051/create-v1/DEV051_CREATE_V1_COMPATIBILITY_STATUS.json').read_text())
    prior=authority['SOURCE_HASHES']['/Users/kevinkeaveney/Production/flex-main-workers/src/analysis/pump_fun_post_migration_analyzer.py']
    assert hashlib.sha256(reconstructed.encode()).hexdigest()==prior
    assert current.count('"maxSupportedTransactionVersion": 1')==3


def test_signature_contract():
    from src.core.deep_walkback import valid_signature
    assert valid_signature(SIG)
    assert not valid_signature('malformed')
    assert not valid_signature(None)
