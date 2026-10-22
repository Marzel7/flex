"""DEV004: synthetic credentials, blocked network/DB, no runtime startup."""
import ast
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import types
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.utils import birdeye_credentials as credentials

ROOTS=[ROOT,*(ROOT/'runtime').iterdir(),*(ROOT/'integration_sources').iterdir()]
ROOTS=[p for p in ROOTS if (p/'src/utils/birdeye_credentials.py').exists()]
BINDINGS=[p/'src/ops/token_data_provider_bindings.py' for p in ROOTS if (p/'src/ops/token_data_provider_bindings.py').exists()]
PRICES=[p/'src/core/price_service.py' for p in ROOTS if (p/'src/core/price_service.py').exists()]
APPROVED='synthetic-approved-paid-key'
LEGACY='synthetic-legacy-key'


def load(path):
 name='dev004_'+str(abs(hash(str(path))))
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
 sys.modules[name]=module;spec.loader.exec_module(module);return module


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
 def denied(*a,**k):raise AssertionError('LIVE_NETWORK_OR_DATABASE_ACCESS_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',denied)
 monkeypatch.setattr(socket,'create_connection',denied)
 monkeypatch.setattr(urllib.request,'urlopen',denied)
 monkeypatch.setattr(sqlite3,'connect',denied)
 # The process starts with env -i. Nothing here reads a real credential.
 monkeypatch.setenv('BIRDEYE',APPROVED)
 monkeypatch.setenv('BIRDEYE_KK',LEGACY)
 monkeypatch.setenv('BIRDEYE_API_KEY','synthetic-alternative-key')
 monkeypatch.setenv('HELIUS_RPC_URL','https://synthetic.invalid/rpc')
 original=Path.read_text;dotenv={'text':None}
 def read(path,*a,**k):
  if path.name=='.env':
   if dotenv['text'] is None:raise FileNotFoundError('synthetic absent config')
   return dotenv['text']
  return original(path,*a,**k)
 monkeypatch.setattr(Path,'read_text',read)
 yield dotenv


@pytest.mark.parametrize('root',ROOTS,ids=lambda p:p.name if p!=ROOT else 'canonical')
def test_helpers_select_only_approved_environment(root,isolation):
 m=load(root/'src/utils/birdeye_credentials.py')
 assert m.birdeye_credential()==APPROVED
 assert m.validated_birdeye_credential(APPROVED)==APPROVED
 with pytest.raises(RuntimeError,match='OVERRIDE_FORBIDDEN'):m.validated_birdeye_credential(LEGACY)


@pytest.mark.parametrize('bad',[None,'','   ',"''",'""'])
@pytest.mark.parametrize('root',ROOTS,ids=lambda p:str(p.relative_to(ROOT)) or 'canonical')
def test_missing_or_blank_never_uses_legacy(root,bad,monkeypatch,isolation):
 if bad is None:monkeypatch.delenv('BIRDEYE')
 else:monkeypatch.setenv('BIRDEYE',bad)
 m=load(root/'src/utils/birdeye_credentials.py')
 with pytest.raises(RuntimeError,match='BIRDEYE_CONFIGURATION_REQUIRED'):m.birdeye_credential()


@pytest.mark.parametrize('root',ROOTS,ids=lambda p:str(p.relative_to(ROOT)) or 'canonical')
def test_same_named_dotenv_source_and_legacy_only_failure(root,monkeypatch,isolation):
 monkeypatch.delenv('BIRDEYE');m=load(root/'src/utils/birdeye_credentials.py')
 isolation['text']='BIRDEYE_KK='+LEGACY+'\nBIRDEYE_API_KEY=synthetic-alternative\n'
 with pytest.raises(RuntimeError,match='CONFIGURATION_REQUIRED'):m.birdeye_credential()
 isolation['text']+='export BIRDEYE="'+APPROVED+'" # synthetic fixture\n'
 assert m.birdeye_credential()==APPROVED
 # Explicit blank env must not silently fall through to any file value.
 monkeypatch.setenv('BIRDEYE',' ')
 with pytest.raises(RuntimeError,match='CONFIGURATION_REQUIRED'):m.birdeye_credential()


@pytest.mark.parametrize('path',BINDINGS,ids=lambda p:str(p.relative_to(ROOT)))
def test_candle_opening_lifecycle_headers_and_single_attempt(path,isolation):
 m=load(path);calls=[]
 def transport(req,*,timeout_seconds):
  calls.append((req,timeout_seconds));return m.ProviderTransportOutcome(429,None,{'Retry-After':'3'},'HTTP_429')
 b=m.BirdeyeProductionBinding(transport=transport)
 for interval in ['1s','1m','15m','1h','4h','1d']:
  req=m.build_birdeye_ohlcv_request(address='synthetic-mint',interval=interval,time_from=100,time_to=102)
  before=len(calls);result=b(req)
  assert result.status_code==429 and result.retry_after_seconds==3
  assert len(calls)==before+1
  sent,timeout=calls[-1];assert sent.get_header('X-api-key')==APPROVED
  assert timeout==45 and '/defi/v3/ohlcv?' in sent.full_url
  assert req['request_parameters']==dict(address='synthetic-mint',chart_type='mcap',currency='usd',mode='range',padding='false',time_from=100,time_to=102,type=interval)
 # Public registry uses the same approved source.
 registry=m.production_provider_bindings(birdeye_transport=transport)
 registry[('Birdeye','/defi/v3/ohlcv')](req)
 assert calls[-1][0].get_header('X-api-key')==APPROVED


@pytest.mark.parametrize('path',BINDINGS,ids=lambda p:str(p.relative_to(ROOT)))
def test_binding_rejects_override_and_missing_before_transport(path,monkeypatch,isolation):
 m=load(path);calls=[]
 with pytest.raises(RuntimeError,match='OVERRIDE_FORBIDDEN'):m.BirdeyeProductionBinding(LEGACY,transport=lambda *a,**k:calls.append(a))
 monkeypatch.delenv('BIRDEYE')
 with pytest.raises(RuntimeError):m.BirdeyeProductionBinding(transport=lambda *a,**k:calls.append(a))
 assert not calls


@pytest.mark.parametrize('path',[p for p in BINDINGS if 'integration_sources' in str(p)],ids=lambda p:str(p.relative_to(ROOT)))
def test_existing_overlay_label_guard_is_preserved(path,isolation):
 m=load(path)
 with pytest.raises(ValueError,match='UNAUTHORIZED'):m.BirdeyeProductionBinding(credential_label='BIRDEYE_KK')
 with pytest.raises(RuntimeError):m.birdeye_credential({'BIRDEYE_KK':LEGACY})
 assert m.birdeye_credential({'BIRDEYE':APPROVED,'BIRDEYE_KK':LEGACY})==APPROVED


def price_module(path,monkeypatch):
 fake=types.ModuleType('src.utils.db_locking')
 fake.db_connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('DATABASE_FORBIDDEN'))
 monkeypatch.setitem(sys.modules,'src.utils.db_locking',fake)
 return load(path)


@pytest.mark.parametrize('path',PRICES,ids=lambda p:str(p.relative_to(ROOT)))
def test_sync_token_price_authorization_and_timeout(path,monkeypatch,isolation):
 m=price_module(path,monkeypatch);calls=[]
 class Response:
  status_code=200
  def json(self):return {'data':{'price':2,'priceInSOL':.01}}
 class Session:
  def __init__(self):self.headers={}
  def get(self,url,**kwargs):calls.append((url,kwargs));return Response()
 monkeypatch.setattr(m.requests,'Session',Session)
 service=object.__new__(m.TokenPriceService);service._birdeye_local=threading.local()
 assert service._fetch_birdeye_sync('synthetic-mint').price_usd==2
 assert len(calls)==1 and calls[0][0].endswith('/defi/token_price')
 assert calls[0][1]=={'params':{'address':'synthetic-mint'},'headers':{'X-API-KEY':APPROVED,'x-chain':'solana'},'timeout':1.0}
 monkeypatch.delenv('BIRDEYE')
 with pytest.raises(RuntimeError,match='CONFIGURATION_REQUIRED'):service._fetch_birdeye_sync('synthetic-mint')
 assert len(calls)==1


@pytest.mark.parametrize('path',PRICES,ids=lambda p:str(p.relative_to(ROOT)))
def test_async_token_price_authorization_and_timeout(path,monkeypatch,isolation):
 m=price_module(path,monkeypatch);calls=[]
 class Response:
  status=200
  async def __aenter__(self):return self
  async def __aexit__(self,*a):pass
  async def json(self):return {'data':{'price':3,'priceInSOL':.02}}
 class Session:
  async def __aenter__(self):return self
  async def __aexit__(self,*a):pass
  def get(self,url,**kwargs):calls.append((url,kwargs));return Response()
 monkeypatch.setattr(m.aiohttp,'ClientSession',Session)
 assert asyncio.run(m.BirdeyeClient.get_price('synthetic-mint')).price_usd==3
 assert calls[0][1]['headers']['X-API-KEY']==APPROVED
 assert calls[0][1]['params']=={'address':'synthetic-mint'} and calls[0][1]['timeout'].total==1.0
 monkeypatch.delenv('BIRDEYE')
 with pytest.raises(RuntimeError,match='CONFIGURATION_REQUIRED'):asyncio.run(m.BirdeyeClient.get_price('synthetic-mint'))
 assert len(calls)==1


def test_current_market_cap_binding_uses_same_authority(isolation):
 path=ROOT/'integration_sources/monitor_worker/src/ops/token_data_provider_bindings.py';m=load(path);calls=[]
 def transport(req,**kwargs):calls.append(req);return m.ProviderTransportOutcome(200,{'success':True,'data':{'marketCap':10}}, {})
 m.BirdeyeProductionBinding(endpoint='https://public-api.birdeye.so/defi/token_overview',transport=transport)(m.build_birdeye_token_overview_request(address='synthetic-mint'))
 assert len(calls)==1 and calls[0].get_header('X-api-key')==APPROVED
 assert calls[0].full_url.endswith('/defi/token_overview?address=synthetic-mint')


def test_pilot_explicit_and_environment_paths_use_authority(monkeypatch,isolation):
 from runtime.creator_resolution.src.ops.early_buyer_bundle_real_pilot import RealProviderTransport
 calls=[]
 class Response:
  status=200;headers={}
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self):return b'{"data":{"items":[]}}'
 def opener(req,timeout):calls.append((req,timeout));return Response()
 for t in [RealProviderTransport.from_environment(opener=opener),RealProviderTransport(APPROVED,'https://synthetic.invalid/rpc',opener=opener)]:
  t.birdeye({'params':{'address':'synthetic-mint','limit':20}})
  assert calls[-1][0].get_header('X-api-key')==APPROVED and calls[-1][1]==30
 with pytest.raises(RuntimeError,match='OVERRIDE_FORBIDDEN'):RealProviderTransport(LEGACY,'https://synthetic.invalid/rpc',opener=opener)
 monkeypatch.delenv('BIRDEYE')
 with pytest.raises(RuntimeError,match='CONFIGURATION_REQUIRED'):RealProviderTransport.from_environment(opener=opener)
 assert len(calls)==2


def test_opening_and_monitor_default_constructor_use_tested_binding(isolation):
 # Execute unchanged consumer class definitions, excluding service/DB imports.
 path=ROOT/'runtime/creator_resolution/src/ops/operation_monitor_worker.py'
 tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MonitorBirdeyeTransport')
 m=load(ROOT/'runtime/creator_resolution/src/ops/token_data_provider_bindings.py');calls=[]
 def transport(req,**kwargs):
  calls.append(req);return m.ProviderTransportOutcome(200,{'data':{'items':[{'unixTime':101,'c':12,'o':11,'h':13,'l':10}]}},{})
 ns={'BirdeyeProductionBinding':lambda:m.BirdeyeProductionBinding(transport=transport),'build_birdeye_ohlcv_request':m.build_birdeye_ohlcv_request,'time':types.SimpleNamespace(time=lambda:200),'json':json}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
 worker=ns['MonitorBirdeyeTransport']()
 entry,request=worker.acquire_watchtower_entry({'mint':'synthetic-mint'},{'time_from':100,'time_to':102,'migration_timestamp':100})
 assert entry['timestamp']==101 and request['request_parameters']['type']=='1s'
 report=worker({'mint':'synthetic-mint','entry_timestamp':100,'candle_resolution':'15m'})
 assert report['candles'][0]['mc']==12 and len(calls)==2
 assert all(x.get_header('X-api-key')==APPROVED for x in calls)


def test_lifecycle_constructor_uses_tested_binding(isolation):
 path=ROOT/'runtime/creator_resolution/src/ops/watchtower_terminal_ath_finalizer.py'
 tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='WatchtowerTerminalAthFinalizer')
 m=load(ROOT/'runtime/creator_resolution/src/ops/token_data_provider_bindings.py');calls=[]
 def factory():return m.BirdeyeProductionBinding(transport=lambda req,**k:calls.append(req))
 ns={'BirdeyeProductionBinding':factory,'time':types.SimpleNamespace(time=lambda:200),'Any':object}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec',dont_inherit=True,flags=__import__('__future__').annotations.compiler_flag),ns)
 lifecycle=ns['WatchtowerTerminalAthFinalizer']('synthetic-no-db')
 lifecycle.binding(m.build_birdeye_ohlcv_request(address='synthetic-mint',interval='15m',time_from=100,time_to=200))
 assert len(calls)==1 and calls[0].get_header('X-api-key')==APPROVED


def test_errors_and_logs_never_contain_credential_material(capsys,caplog,isolation):
 with pytest.raises(RuntimeError) as err:credentials.validated_birdeye_credential(LEGACY)
 text=str(err.value)+repr(capsys.readouterr())+caplog.text
 assert APPROVED not in text and LEGACY not in text


@pytest.mark.parametrize('path',BINDINGS,ids=lambda p:str(p.relative_to(ROOT)))
def test_request_builders_transport_and_budgets_unchanged(path,isolation):
 rel=str(path.relative_to(ROOT));old=ast.parse(subprocess.check_output(['git','show','fde478b7:'+rel],text=True));new=ast.parse(path.read_text())
 def protected(tree):
  out={}
  for n in tree.body:
   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith(('build_','_urlopen_')):out[n.name]=ast.dump(n)
   if isinstance(n,ast.ClassDef) and n.name in ('HeliusProductionBinding','BirdeyeProductionBinding'):
    for f in n.body:
     if isinstance(f,ast.FunctionDef) and (n.name=='HeliusProductionBinding' or f.name=='__call__'):out[n.name+'.'+f.name]=ast.dump(f)
   if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('PROVIDER_TIMEOUT_SECONDS','PROVIDER_BINDING_CONTRACT_VERSION') for t in n.targets):out[ast.unparse(n.targets[0])]=ast.dump(n)
  return out
 assert protected(old)==protected(new)


@pytest.mark.parametrize('path',[p for p in BINDINGS if 'integration_sources' in str(p)],ids=lambda p:str(p.relative_to(ROOT)))
@pytest.mark.parametrize('bad',['', '   ', "'   '", '\"   \"'])
def test_overlay_empty_credentials_fail_closed(path,bad,monkeypatch,isolation):
 m=load(path);monkeypatch.setenv('BIRDEYE',bad)
 with pytest.raises(RuntimeError):m.BirdeyeProductionBinding()


def test_no_legacy_credential_reads_remain_in_acquisition_sources(isolation):
 for path in BINDINGS+[p/'src/utils/birdeye_credentials.py' for p in ROOTS]:
  tree=ast.parse(path.read_text())
  for node in ast.walk(tree):
   if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('get','getenv') and node.args:
    first=node.args[0]
    assert not (isinstance(first,ast.Constant) and first.value in ('BIRDEYE_KK','BIRDEYE_API_KEY','BIRDEYE_TOKEN'))
