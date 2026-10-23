from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0].replace("(out/'ops.sqlite').resolve()]", "(out/'ops.sqlite').resolve(),(out/'creator.sqlite').resolve()]"),'isolation','exec'))
os.environ['CREATOR_DB_PATH']=str(out/'creator.sqlite')
# Existing secondary creator repository is a declared disposable binding.
# The shared guard allows Core/OPS only: keep that strict and report any access.
result={'status':'HOLD','database_attempts':db_attempts,'blocked':blocked}
try:
 import src.core.pumpfun_curve_listener as m
 import asyncio
 scheduled=[];threads=[]
 # Hold background tasks before their first instruction; test constructor/schema only.
 real_create=asyncio.create_task
 def hold_task(coro,*a,**kw):
  scheduled.append(coro.cr_code.co_name);coro.close()
 asyncio.create_task=hold_task
 old_start=threading.Thread.start
 def hold_thread(self,*a,**kw):
  if self.name=='db-metrics-snapshot':threads.append(self.name);return
  return old_start(self,*a,**kw)
 threading.Thread.start=hold_thread
 async def startup():
  instance=m.PumpFunCurveListener()
  def gather_boundary(*coros,**kw):
   for coro in coros:
    scheduled.append(coro.cr_code.co_name);coro.close()
   async def ready():return []
   return ready()
  asyncio.gather=gather_boundary
  await instance.listen()
  return instance
 listener=asyncio.run(startup())
 result.update(held_background_tasks=scheduled,held_threads=threads)
 result['status']='PASS_LISTEN_GATHER_BEFORE_TASK_EXECUTION'
except BaseException as e:result.update(error=str(e),traceback=traceback.format_exc(limit=10))
(out/'listener-constructor.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
