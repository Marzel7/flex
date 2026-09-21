"""Signal-driven committed materialization and semantic spool consumers."""
import asyncio
from src.ops.pumpswap_materialized_semantic_transformer import transform_all
from src.ops.pumpswap_prospective_semantic_capture import process_one
class Workers:
 def __init__(self,materialized,semantic,env):self.materialized,self.semantic,self.env=materialized,semantic,env;self.q=asyncio.Queue(maxsize=1);self.metrics={'transforms':0,'results':0,'failures':0}
 def signal(self):
  try:self.q.put_nowait(True)
  except asyncio.QueueFull:pass
 async def run(self):
  while True:
   await self.q.get()
   try:
    out=await asyncio.to_thread(transform_all,self.materialized,self.semantic,env=self.env);self.metrics['transforms']+=out['transformed']
    while await asyncio.to_thread(process_one,self.semantic):self.metrics['results']+=1
   except asyncio.CancelledError:raise
   except Exception:self.metrics['failures']+=1
def submission_capability():return 'NONE'
