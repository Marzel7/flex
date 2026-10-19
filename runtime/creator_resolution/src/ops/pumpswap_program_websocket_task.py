"""Independent, disabled PumpSwap program-log research websocket task."""
from __future__ import annotations
import asyncio,time
from dataclasses import dataclass,field
from typing import Any,Awaitable,Callable,Mapping
from src.ops.pumpswap_generic_subscription import adapt_notification,subscription_request
from src.ops.pumpswap_raw_event_retention import FEATURE_FLAG,enabled

COMMITMENT='confirmed';MAX_BACKOFF_SECONDS=60;INITIAL_BACKOFF_SECONDS=2
@dataclass
class Metrics:
 health:str='DISABLED';connection_attempts:int=0;connections:int=0;subscription_acks:int=0;notifications:int=0;accepted:int=0;duplicates:int=0;malformed:int=0;disconnects:int=0;reconnects:int=0;backoff:int=0;last_notification_at:float|None=None;last_commit_at:float|None=None
class PumpSwapProgramWebsocketTask:
 def __init__(self,*,endpoint:str,store_path:str,connector:Callable[[str],Awaitable[Any]],env:Mapping[str,str]|None=None,on_commit:Callable[[],None]|None=None,session=None):self.endpoint,self.store_path,self.connector,self.env,self.on_commit,self.session=endpoint,store_path,connector,env,on_commit,session;self.metrics=Metrics()
 async def run(self)->None:
  if not enabled(self.env):return
  delay=INITIAL_BACKOFF_SECONDS;self.metrics.health='CONNECTING'
  while True:
   ws=None
   try:
    self.metrics.connection_attempts+=1;ws=await self.connector(self.endpoint);self.metrics.connections+=1
    await ws.send(__import__('json').dumps(subscription_request()))
    ack=__import__('json').loads(await ws.recv())
    if not ack.get('result') or ack.get('id')!=1:raise RuntimeError('SUBSCRIPTION_ACK_INVALID')
    self.metrics.subscription_acks+=1;self.metrics.health='HEALTHY';delay=INITIAL_BACKOFF_SECONDS
    while True:
     item=__import__('json').loads(await ws.recv());self.metrics.notifications+=1;self.metrics.last_notification_at=time.time()
     result=adapt_notification(item,store_path=self.store_path,env=self.env,session=self.session)
     self.metrics.accepted+=result.get('accepted',0);self.metrics.duplicates+=result.get('duplicate_or_rejected',0);self.metrics.malformed+=result.get('malformed',0)
     if result.get('accepted'):
      self.metrics.last_commit_at=time.time()
      if self.on_commit:self.on_commit()
   except asyncio.CancelledError:
    self.metrics.health='STOPPED';raise
   except Exception:
    self.metrics.disconnects+=1;self.metrics.reconnects+=1;self.metrics.health='DEGRADED';self.metrics.backoff=delay
    try:await asyncio.sleep(delay)
    except asyncio.CancelledError:self.metrics.health='STOPPED';raise
    delay=min(MAX_BACKOFF_SECONDS,delay*2);self.metrics.health='CONNECTING'
   finally:
    if ws is not None:
     try:await ws.close()
     except Exception:pass
def submission_capability():return 'NONE'
