from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolation','exec'))
os.environ['MONITOR_RUNTIME']='dev'
def allowed_thread(self,*a,**kw):
 if self.name=='db-writer-live:'+str(out/'ops.sqlite'):return original_start(self,*a,**kw)
 return start_thread(self,*a,**kw)
threading.Thread.start=allowed_thread
from src.ops import operation_monitor_worker as w,watchtower_terminal_ath_finalizer as f
from src.ops.token_data_provider_bindings import ProviderTransportOutcome
START=1800000000;MINT='DEV004_SYNTHETIC_RECOVERY_LAUNCH';fault=os.environ.get('CRASH_AT','');events=out/'events.jsonl'
def record(row):
 with events.open('a') as stream:stream.write(json.dumps(row,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
calls=0;commits=0
original_admit=w.DevProviderBudget.admit
def admission(self,*a,**kw):
 r=original_admit(self,*a,**kw);record({'kind':'reserved','count':r['token_calls']})
 if fault=='before_dispatch':os._exit(71)
 return r
w.DevProviderBudget.admit=admission

def transport(request):
 global calls
 calls+=1;p=request['request_parameters'];left=p['time_from'];idx=(left-START)//900
 record({'kind':'synthetic_response','from':left,'to':p['time_to']})
 if fault=='after_response':os._exit(71)
 if os.environ.get('PROVIDER_FAILURE')=='1':return ProviderTransportOutcome(503,{}, {},'HTTP_503')
 candle=[(100,200,80,150),(150,500,140,250),(250,1000,5,10)][idx]
 return ProviderTransportOutcome(200,{'data':{'items':[{'unixTime':left,'o':candle[0],'h':candle[1],'l':candle[2],'c':candle[3]}]}},{})
real_window=f.completion.commit_window
real_final=f.completion.commit_finalization
real_transaction=f.completion.transaction
def transaction(path,label,fn):
 if fault=='before_checkpoint' and label=='watchtower-terminal-ath-chunk':
  def interrupted(c):
   fn(c);record({'kind':'checkpoint_before_commit'});os._exit(71)
  return real_transaction(path,label,interrupted)
 return real_transaction(path,label,fn)
f.completion.transaction=transaction
def committed_window(*a,**kw):
 global commits
 result=real_window(*a,**kw);assert result.committed
 commits+=1;record({'kind':'chunk_committed','id':a[5]})
 if fault=='after_checkpoint' and commits==1:os._exit(71)
 if fault=='between_windows' and commits==2:os._exit(71)
 return result
def committed_final(*a,**kw):
 result=real_final(*a,**kw);assert result.committed
 record({'kind':'finalization_committed','id':a[4]})
 if fault=='before_ack':os._exit(71)
 return result
f.completion.commit_window=committed_window
f.completion.commit_finalization=committed_final
class PlannedFinalizer(f.WatchtowerTerminalAthFinalizer):
 def __init__(self,path,**kwargs):super().__init__(path,binding=transport,**kwargs)
 def finalize(self,mint,**kwargs):return super().finalize(mint,chunk_seconds=900,**kwargs)
if os.environ.get('DIRECT_CHECKPOINT')=='1':
 chunk=f._digest({'version':f.FINALIZER_VERSION,'mint':MINT,'resolution':'15m','time_from':START,'time_to':START+900})
 candles=[{'timestamp':START,'open':100.0,'high':200.0,'low':80.0,'close':150.0}]
 f.completion.commit_window(str(out/'ops.sqlite'),MINT,'15m',START,START+900,chunk,candles,START)
 f.completion.commit_window(str(out/'ops.sqlite'),MINT,'15m',START,START+900,chunk,candles,START)
 print('DIRECT_CHECKPOINT_PASS');sys.exit(0)
q=w.MonitorQueue(out/'queue',enabled=True)
if os.environ.get('RECOVER')=='1':q.queue.recover()
with sqlite3.connect(str(out/'ops.sqlite')) as c:
 c.row_factory=sqlite3.Row;fact=dict(c.execute("select * from operation_monitor_facts where operation_id='watchtower' and mint=?",(MINT,)).fetchone())
admission_result=q.enqueue_terminal_ath_finalization(fact)
# Re-delivery uses original immutable logical identity even after finalization.
if os.environ.get('DUPLICATE_FINALIZED')=='1':
 ident=f.WatchtowerTerminalAthFinalizer.logical_job_identity(fact);q.queue.enqueue({'work_type':'WATCHTOWER_TERMINAL_ATH_FINALIZATION','mint':MINT,'logical_identity':ident},message_id=ident)
worker=w.MonitorWorker(q,db_path=str(out/'ops.sqlite'),terminal_finalizer_factory=PlannedFinalizer)
result={'processed':worker.process_once(),'state':q.queue.depth(),'provider_calls':calls,'admission':admission_result,'database_attempts':db_attempts,'blocked':blocked}
(out/'child-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
