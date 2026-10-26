"""Installed-loop AST qualification: synthetic sockets only, no runtime import."""
import ast
import asyncio
import errno
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import time
import types
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'runtime/listener/src/core/pumpfun_curve_listener.py'
class Closed(ConnectionError):pass
class InvalidStatus(Exception):
 def __init__(self,status):self.status_code=status

@pytest.fixture
def harness(monkeypatch):
 monkeypatch.setattr(socket.socket,'connect',lambda *a: (_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 tree=ast.parse(SOURCE.read_text());names={'_pumpswap_failure','PumpSwapConfigurationError','SubscriptionUnavailable','listen_pumpswap_websocket'}
 nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name in names]
 logs=[];states=[];delays=[];outcomes=[];heartbeats=[];scheduled=[]
 real_sleep=asyncio.sleep
 async def sleep(delay):
  delays.append(delay);await real_sleep(0)
  if len(delays)>=40:raise asyncio.CancelledError()
 async def wait_for(coro,timeout):return await coro
 class WS:
  def __init__(self,messages):self.messages=list(messages)
  async def send(self,value):pass
  async def recv(self):
   if self.messages:
    v=self.messages.pop(0)
    if isinstance(v,Exception):raise v
    return json.dumps(v)
   raise Closed('secret-token-and-url-must-not-leak')
  async def close(self):pass
 class Connect:
  def __init__(self,endpoint,**kw):self.item=outcomes.pop(0) if outcomes else ConnectionRefusedError(errno.ECONNREFUSED,'https://secret/?api-key=TOKEN')
  async def __aenter__(self):
   if isinstance(self.item,Exception):raise self.item
   return WS(self.item)
  async def __aexit__(self,*a):pass
 ns={'socket':socket,'os':os,'time':time,'json':json,'asyncio':types.SimpleNamespace(sleep=sleep,wait_for=wait_for,TimeoutError=TimeoutError),'websockets':types.SimpleNamespace(connect=Connect,exceptions=types.SimpleNamespace(ConnectionClosed=Closed)), 'log_print':lambda *a,**k:logs.append(' '.join(map(str,a))),'premig_log':lambda *a:None,'_record_resolver_snapshot':lambda **kw:None,'record_request':lambda **kw:None,'record_wss':lambda *a,**kw:None,'PUMPFUN_MIGRATION_ACCOUNT':'fixture','get_migration_setting':lambda *a:True}
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
 async def toggle(*a):pass
 w=types.SimpleNamespace(_wait_for_launch_toggle=toggle,_websocket_endpoints=lambda:[('wss://example.invalid/?api-key=SECRET','fixture'),('wss://public.invalid','public')],_acquisition_availability=types.SimpleNamespace(transition=lambda *a:states.append(a)),websocket_msg_count=0,websocket_migration_count=0,_schedule_journal_migration=lambda *a,**kw:scheduled.append((a,kw)),_debug_pumpswap_migration_skip=lambda *a:None)
 async def run():
  async def birth():
   for i in range(50):heartbeats.append(i);await real_sleep(0)
  async def pumpswap():
   try:await ns['listen_pumpswap_websocket'](w)
   except asyncio.CancelledError:pass
  await asyncio.gather(birth(),pumpswap())
 return types.SimpleNamespace(ns=ns,logs=logs,states=states,delays=delays,outcomes=outcomes,w=w,heartbeats=heartbeats,scheduled=scheduled,run=lambda:asyncio.run(run()))


def diagnostics(h):return [json.loads(l.split('] ',1)[1]) for l in h.logs if l.startswith('[PUMPSWAP_RECOVERY]')]

def test_prolonged_outage_keeps_birth_task_and_bounded_backoff(harness):
 h=harness;h.run();d=diagnostics(h)
 assert len(d)==40 and d[9]['consecutive_failures']==10 and d[-1]['consecutive_failures']==40
 assert len(h.heartbeats)==50 and all(5<=x<=60 for x in h.delays) and h.delays[-1]==60
 assert not any(s[1]=='AVAILABLE' for s in h.states)
 assert 'TOKEN' not in '\n'.join(h.logs) and 'SECRET' not in '\n'.join(h.logs) and 'https://' not in '\n'.join(h.logs)


def test_recovery_resets_only_after_verified_subscription(harness):
 h=harness;h.outcomes.extend([ConnectionRefusedError(61,'secret')]*12+[[{'id':1,'result':7}]])
 h.run();d=diagnostics(h);assert d[11]['consecutive_failures']==12 and d[12]['consecutive_failures']==1
 assert any(s[1]=='AVAILABLE' for s in h.states)

@pytest.mark.parametrize('reply',[{'id':1,'result':None},{'id':1,'result':False},{'id':1,'error':{'message':'SECRET'}}])
def test_unverified_subscription_never_resets_or_marks_available(harness,reply):
 h=harness;h.outcomes.extend([ConnectionRefusedError(61,'secret')]*11+[[reply]])
 h.run();assert not any(s[1]=='AVAILABLE' for s in h.states)
 assert diagnostics(h)[-1]['consecutive_failures']>=12

@pytest.mark.parametrize('exc,phase',[ (socket.gaierror(-2,'SECRET'),'DNS'),(ConnectionRefusedError(61,'SECRET'),'TCP'),(ssl.SSLError(1,'SECRET'),'TLS'),(InvalidStatus(503),'WEBSOCKET_HANDSHAKE')])
def test_safe_classification(harness,exc,phase):
 d=harness.ns['_pumpswap_failure'](exc,'CONNECT',0,12,60);assert d['phase']==phase and d['recoverable'];assert 'SECRET' not in json.dumps(d)

@pytest.mark.parametrize('exc',[ssl.SSLCertVerificationError(1,'SECRET'),InvalidStatus(403)])
def test_configuration_blocks_channel_without_killing_birth_task(harness,exc):
 h=harness;h.outcomes.append(exc)
 if isinstance(exc,InvalidStatus):h.w._websocket_endpoints=lambda:[('wss://fixture.invalid','fixture')]
 h.run();assert h.states[-1][-1]=='PUMPSWAP_CONFIGURATION_ERROR' and len(h.heartbeats)==50

@pytest.mark.parametrize('exc',[RuntimeError('WRONG_GENERATION'),sqlite3.DatabaseError('CORRUPT'),RuntimeError('DISK_FLOOR'),OSError(errno.ENOSPC,'DISK_FULL'),ValueError('authority')])
def test_essential_failures_are_not_retried(harness,exc):
 h=harness;h.outcomes.append(exc)
 with pytest.raises(type(exc)):h.run()
 assert not h.delays


def test_restart_starts_down_and_requires_new_confirmation(harness):
 h=harness;h.run();h.delays.clear();h.run();assert not any(s[1]=='AVAILABLE' for s in h.states)


def test_migration_notification_reaches_existing_durable_schedule_only(harness):
 h=harness;h.outcomes.append([{'id':1,'result':7},{'params':{'subscription':7,'result':{'value':{'logs':['Instruction: Migrate'],'signature':'fixture-signature','err':None}}}}]);h.run()
 assert len(h.scheduled)==1 and h.scheduled[0][0][0]=='fixture-signature'
 # Admission/ack ownership stays in unchanged _schedule_journal_migration.
 assert h.scheduled[0][1]['notification_source']=='PUMPSWAP_MIGRATION_ACCOUNT'


@pytest.mark.parametrize('endpoint',['https://wrong.invalid','not-a-url'])
def test_invalid_endpoint_is_channel_down_not_healthy(harness,endpoint):
 h=harness;h.w._websocket_endpoints=lambda:[(endpoint,'fixture')];h.run()
 assert not h.delays and h.states[-1][-1]=='PUMPSWAP_CONFIGURATION_ERROR' and len(h.heartbeats)==50


def test_unrelated_listener_contracts_byte_identical():
 import subprocess
 original=subprocess.check_output(['git','show','b91473f7:runtime/listener/src/core/pumpfun_curve_listener.py'],text=True)
 def without_loop(text):
  start=text.index('    async def listen_pumpswap_websocket');end=text.index('    async def _seed_trade_subscriptions',start)
  return text[:start]+text[end:]
 changed=SOURCE.read_text();start=changed.index('def _pumpswap_failure');end=changed.index('class MigrationTerminalDurabilityRisk',start)
 changed=changed[:start]+changed[end:]
 assert without_loop(changed)==without_loop(original)



def test_dns_snapshot_also_receives_sanitized_exception(harness):
 h=harness;captured=[];h.ns['_record_resolver_snapshot']=lambda **kw:captured.append(kw)
 h.outcomes.append(socket.gaierror(-2,'TOKEN_SECRET'));h.run()
 failures=[r for r in captured if r['event']=='resolver_failure']
 assert failures and str(failures[0]['exc'])=='[Errno -2] DNS_FAILURE'
