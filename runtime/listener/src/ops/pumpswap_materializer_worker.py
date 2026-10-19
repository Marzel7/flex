"""Event-driven, single-concurrency post-commit PumpSwap materializer worker."""
from __future__ import annotations
import asyncio
from typing import Callable, Mapping, Any
from src.ops.pumpswap_raw_materializer import materialize_one

class PumpSwapMaterializerWorker:
    def __init__(self, *, raw_store: str, materialization_store: str,
                 resolver: Callable[[str], Mapping[str, Any]], env=None,on_commit=None):
        self.raw_store,self.materialization_store,self.resolver,self.env,self.on_commit=raw_store,materialization_store,resolver,env,on_commit
        self._signals=asyncio.Queue(maxsize=1);self.metrics={'signals':0,'processed':0,'failures':0,'last_success_at':None}
    def signal(self):
        try:self._signals.put_nowait(True);self.metrics['signals']+=1
        except asyncio.QueueFull:pass
    async def run(self):
        while True:
            await self._signals.get()
            try:
                result=await asyncio.to_thread(materialize_one,self.raw_store,self.materialization_store,self.resolver)
                if result:
                 self.metrics['processed']+=1;self.metrics['last_success_at']=__import__('time').time();self.signal()
                 if self.on_commit:self.on_commit()
            except asyncio.CancelledError:raise
            except Exception:self.metrics['failures']+=1
def submission_capability():return 'NONE'
