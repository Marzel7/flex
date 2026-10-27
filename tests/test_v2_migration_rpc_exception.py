"""Installed listener methods, real disposable journal, no runtime/network startup."""
import ast, asyncio, types
import pytest
from tests.test_v2_helius_rpc_budget import e
from tests.test_v2_migration_completion import env
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]/'runtime/listener/src/core/pumpfun_curve_listener.py'

def attach(env):
    names={'call_discovery_rpc','_get_transaction_cached','handle_migration'}
    nodes=[n for n in ast.walk(ast.parse(SOURCE.read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name in names]
    for n in nodes:n.decorator_list=[]
    import typing
    env.ns.update(Optional=typing.Optional,Dict=typing.Dict)
    env.ns['log_print']=lambda *a,**kw:None
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),env.ns)
    for n in names:setattr(env.w,n,types.MethodType(env.ns[n],env.w))
    env.w.discovery_rpc_semaphore=asyncio.Semaphore(1)
    env.w.tx_cache={};env.w.tx_cache_stats=dict(hit=0,miss=0,wait=0);env.w.tx_inflight_locks={};env.w.tx_cache_ttl_seconds=60
    env.w._extract_mint_from_logs=lambda _:None

@pytest.mark.parametrize('kind',['budget','timeout','unknown'])
def test_exact_exception_survives_discovery_and_cache(e,env,kind):
    attach(env);error={'budget':e.m.BudgetDeferred('HELIUS_INVESTIGATION_REQUIRED'),'timeout':TimeoutError('timeout'),'unknown':ValueError('bad transaction response')}[kind]
    async def rpc(*a,**kw):raise error
    env.w.__internal_rpc=rpc
    async def run():
        with pytest.raises(type(error)) as caught:await env.w._get_transaction_cached('sig')
        assert caught.value is error
        assert not env.w.tx_inflight_locks
    asyncio.run(run())

def test_actual_handler_runner_budget_refund_restart_duplicate(e,env):
    attach(env);e.open()
    async def rpc(*a,**kw):raise e.m.BudgetDeferred('HELIUS_INFLIGHT_LIMIT')
    env.w.__internal_rpc=rpc
    async def run():
        task=env.w._schedule_journal_migration('sig',[],notification_mint='subject')
        assert await task=='BUDGET_DEFERRED'
        assert env.w._schedule_journal_migration('sig',[],notification_mint='subject') is None
    asyncio.run(run());row=env.m.MigrationJournal(env.j.path).snapshot()[0]
    assert row['attempts']==0 and row['error_code']=='HELIUS_BUDGET_DEFERRED'
    assert row['retry_not_before']==1300 and row['ack'] is None and row['notification_mint']=='subject'
    assert not env.w.tx_cache_pending_retries

@pytest.mark.parametrize('error',[TimeoutError('timeout'),ValueError('invalid')])
def test_acquisition_error_not_fabricated_missing_or_delayed(e,env,error):
    attach(env)
    async def rpc(*a,**kw):raise error
    env.w.__internal_rpc=rpc
    assert asyncio.run(env.w.handle_migration('sig',[]))=='FAILED'
    assert not env.w.tx_cache_pending_retries

def test_genuine_missing_response_stays_missing(e,env,monkeypatch):
    attach(env)
    async def rpc(*a,**kw):return {'result':None}
    env.w.__internal_rpc=rpc
    # No sleep needed to verify actual result:null, not an exception.
    monkeypatch.setenv('LISTENER_GETTX_RETRY_DELAYS','0')
    assert asyncio.run(env.w._get_transaction_cached('sig')) is None
    assert env.w.tx_cache_stats['miss']==1

@pytest.mark.parametrize('kind',['budget','timeout','unknown','http'])
def test_internal_discovery_failure_classification(e,env,monkeypatch,kind):
    import contextlib,time,typing
    env.ns["Optional"]=typing.Optional
    node=next(n for n in ast.walk(ast.parse(SOURCE.read_text())) if isinstance(n,ast.AsyncFunctionDef) and n.name=='__internal_rpc')
    class Session:
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
    errors={'budget':e.m.BudgetDeferred('HELIUS_INFLIGHT_LIMIT'),'timeout':TimeoutError('read'),'unknown':ValueError('wire')}
    @contextlib.asynccontextmanager
    async def post(*a,**kw):
        if kind!='http':raise errors[kind]
        yield types.SimpleNamespace(status=503)
    monkeypatch.setattr(e.m,'post',post)
    env.ns.update(aiohttp=types.SimpleNamespace(ClientSession=Session,ClientTimeout=lambda **kw:None),time=time,RPC_URLS=['https://mainnet.helius-rpc.com/'],record_request=lambda **kw:None)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),env.ns)
    async def run():
        with pytest.raises(RuntimeError if kind=='http' else type(errors[kind])) as caught:
            await env.ns['__internal_rpc'](env.w,{'method':'getTransaction'},priority='critical')
        if kind!='http':assert caught.value is errors[kind]
        else:assert str(caught.value)=='RPC_HTTP_503'
    asyncio.run(run())

def test_original_installed_counterexample(e,env):
    import subprocess
    text=subprocess.check_output(['git','show','455f160d:runtime/listener/src/core/pumpfun_curve_listener.py'],text=True)
    node=next(n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.AsyncFunctionDef) and n.name=='call_discovery_rpc')
    exec(compile(ast.Module(body=[node],type_ignores=[]),'installed-preimage','exec'),env.ns)
    env.w.discovery_rpc_semaphore=asyncio.Semaphore(1)
    async def refused(*a,**kw):raise e.m.BudgetDeferred('HELIUS_INVESTIGATION_REQUIRED')
    env.w.__internal_rpc=refused
    async def run():
        with pytest.raises(NameError) as caught:await env.ns['call_discovery_rpc'](env.w,'getTransaction',[])
        assert isinstance(caught.value.__context__,e.m.BudgetDeferred)
    asyncio.run(run())

@pytest.mark.parametrize('response,kind,message',[(None,RuntimeError,'RPC_ACQUISITION_UNAVAILABLE'),({},ValueError,'RPC_RESPONSE_INVALID'),({'error':{'code':-32005,'message':'fixture'}},RuntimeError,'RPC_RESPONSE_ERROR_-32005')])
def test_rpc_error_response_is_not_missing_transaction(e,env,response,kind,message):
    attach(env)
    async def rpc(*a,**kw):return response
    env.w.__internal_rpc=rpc
    async def run():
        with pytest.raises(kind,match=message):await env.w._get_transaction_cached('sig')
    asyncio.run(run())
