"""Feature-gated owner for exactly one optional research websocket task."""
import asyncio
from src.ops.pumpswap_raw_event_retention import enabled
class Supervisor:
 def __init__(self):self.task=None;self.launches=0
 def start(self,coro_factory,env):
  if not enabled(env) or self.task and not self.task.done():return False
  self.task=asyncio.create_task(coro_factory());self.launches+=1;return True
 async def stop(self):
  if self.task:self.task.cancel()
  try:
   if self.task:await self.task
  except asyncio.CancelledError:pass
 async def run(self,coro_factory,env):
  """Register exactly one child and make parent cancellation await its cleanup."""
  if not self.start(coro_factory,env):return False
  try:
   await self.task
  finally:
   await self.stop()
  return True
