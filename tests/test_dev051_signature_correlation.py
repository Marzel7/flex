"""Strict identity correlation at every exact CREATE transaction retrieval boundary."""
import asyncio,ast,copy,json
from pathlib import Path
import pytest
import importlib.util,sys
_spec=importlib.util.spec_from_file_location('dev051_parent_contract',Path(__file__).with_name('test_dev051_create_compatibility.py'))
c=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=c;_spec.loader.exec_module(c)
module=c.module
OTHER=json.loads((c.ROOT/'docs/audits/dev051/TX2_decoded.json').read_text())['signature']


def mutate(tx,case,enhanced):
    if enhanced:
        message=tx['transaction']['message'];tx={'signature':c.SIG,'slot':tx['slot'],'blockTime':tx['blockTime'],'accountKeys':message['accountKeys'],'instructions':message['instructions'],'innerInstructions':tx['meta']['innerInstructions']}
        if case=='valid_mismatch':tx['signature']=OTHER
        elif case=='missing':del tx['signature']
        elif case=='malformed':tx['signature']='not-a-signature'
        elif case=='bad_type':tx['signature']=[c.SIG]
        elif case=='wrong_field':del tx['signature'];tx['transaction']={'signatures':[c.SIG]}
    else:
        if case=='valid_mismatch':tx['transaction']['signatures']=[OTHER,c.SIG]
        elif case=='missing':del tx['transaction']['signatures']
        elif case=='malformed':tx['transaction']['signatures']=['not-a-signature']
        elif case=='bad_type':tx['transaction']['signatures']=c.SIG
        elif case=='wrong_field':del tx['transaction']['signatures'];tx['signature']=c.SIG
    return tx


@pytest.mark.parametrize('path',['fetch','blocktime','scan','enhanced'])
@pytest.mark.parametrize('case',['matching_v0','matching_v1','matching_legacy','valid_mismatch','missing','malformed','bad_type','wrong_field'])
def test_exact_retrieval_identity(module,monkeypatch,path,case):
    version=0 if case=='matching_v0' else 'legacy' if case=='matching_legacy' else 1
    tx=mutate(c.retained_shape(version),case,path=='enhanced');calls=[]
    class Session:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):return False
        def post(self,url,json,timeout=None):
            calls.append(copy.deepcopy(json))
            if path=='enhanced':assert json=={'transactions':[c.SIG]};return c.Response([tx])
            assert json['params'][0]==c.SIG and json['params'][1]['maxSupportedTransactionVersion']==1
            return c.Response({'result':tx})
    monkeypatch.setattr(module.aiohttp,'ClientSession',Session)
    monkeypatch.setattr(module,'HELIUS_API_KEY','offline-fixture-only' if path=='enhanced' else '')
    async def history(session,url,payload,timeout_s=30):
        assert payload['method']=='getSignaturesForAddress'
        return {'result':[{'signature':c.SIG,'err':None,'blockTime':c.RETAINED['block_time']}]}
    monkeypatch.setattr(module,'_rpc_post',history)
    a=c.analyzer(module)
    async def run():
        if path=='fetch':return await a.fetch_tx_with_retry(Session(),c.SIG)
        if path=='blocktime':return await a._get_blocktime_for_signature(c.SIG)
        if path=='scan':return await a.extract_bonding_curve_from_creation_tx(max_pages_override=1)
        return await a.extract_bonding_curve_via_helius_parse(c.SIG)
    result=asyncio.run(run());ok=case.startswith('matching')
    assert bool(result)==ok
    assert len(calls)==1  # No mismatch-induced transport retry or budget increase.
    if path in ['scan','enhanced']:
        assert (a._create_tx_signature==c.SIG)==ok
        assert (a._create_tx_validation is not None)==ok
    else:assert a._create_tx_signature is None
    # Returned evidence was never filled or repaired from the request.
    if case=='missing':
        assert ('signature' not in tx) if path=='enhanced' else ('signatures' not in tx['transaction'])


@pytest.mark.parametrize('requested',['bad',None,'0'*88,'1'*90])
def test_malformed_matching_request_and_response_rejected(module,requested):
    tx=c.retained_shape();tx['transaction']['signatures']=[requested]
    assert not c.analyzer(module)._transaction_signature_matches(tx,requested)


def test_matching_signature_does_not_qualify_wrong_mint_program_or_missing_evidence(module,monkeypatch):
    # Original strict validator negatives remain unchanged after correlation.
    for mutation in ['wrong_mint','wrong_program','missing_mint','missing_curve','missing_all']:
        c.test_strict_rejections(module,mutation)


def test_imports_selection_retries_and_qualification_unchanged():
    import hashlib
    source=(c.ROOT/'src/analysis/pump_fun_post_migration_analyzer.py').read_text();tree=ast.parse(source)
    # Reverse exactly the helper+guards, then verify the complete immutable parent file.
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='PostMigrationAnalyzer')
    helper=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='_transaction_signature_matches')
    lines=source.splitlines(keepends=True);del lines[helper.lineno-1:helper.end_lineno+1];prior=''.join(lines)
    for indent,args,action in [('                            ','result, sig','return None'),('                    ','tx, create_tx_sig, enhanced=True','return None'),('        ','tx, sig','return None'),('                    ','tx, sig','continue')]:
        guard=indent+'if not self._transaction_signature_matches('+args+'):\n'+indent+'    '+action+'\n'
        assert prior.count(guard)==1;prior=prior.replace(guard,'')
    authority=json.loads((c.ROOT/'docs/audits/dev051/create-correction/DEV051_CREATE_COMPATIBILITY_CORRECTION_STATUS.json').read_text())
    # This is the qualified parent analyzer hash independently recorded in the readiness manifest.
    assert hashlib.sha256(prior.encode()).hexdigest()=='96d89ec3b8b00accd010008a89c520e598eafc3156addaf278e19c65cdcff3b8'
    assert not any(isinstance(x,(ast.Import,ast.ImportFrom)) for x in ast.walk(helper))


def test_jsonparsed_alt_and_creator_evidence_boundary(module):
    tx=c.retained_shape(1);keys=tx['transaction']['message']['accountKeys'];keys.append({'pubkey':'ResolvedLookupFixture','source':'lookupTable','signer':False,'writable':True})
    a=c.analyzer(module);message,_=a._get_message_and_instructions(tx)
    assert a._transaction_signature_matches(tx,c.SIG)
    assert a._resolve_account_key(message,len(keys)-1)=='ResolvedLookupFixture'
    assert a._validate_pumpfun_create_tx(tx)['is_pumpfun_create']
    assert all('data' not in ix for ix in tx['transaction']['message']['instructions'])
    assert 'creator' not in tx  # Missing Pump-specific creator bytes remain absent.
