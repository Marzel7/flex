import importlib.util,json,pathlib,resource,signal,time,shutil
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000));signal.signal(signal.SIGXFSZ,signal.SIG_IGN)
s=importlib.util.spec_from_file_location('control','/private/tmp/dev004_production_control.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c);c.bind('v1',True)
s=importlib.util.spec_from_file_location('q',c.R/'runtime/listener/src/ops/dev004_quarantine_transfer.py');q=importlib.util.module_from_spec(s);s.loader.exec_module(q)
base=pathlib.Path('/private/tmp/dev004-production-compact-stage-20261024');cp=base/'quarantine-fenced-capacity.json';start=time.monotonic();steps=0
try:
 authorize=lambda:c.g.validate_pair(write_path=c.settings('v1')['WT_OPS_DB_PATH'],purpose='write')
 q.initialize(c.settings('v1')['WT_OPS_DB_PATH'],base/'ops.sqlite',cp,generation=c.P['source_generation'],authorize=authorize)
 while True:
  state=q.step(cp,authorize=authorize);steps+=1
  if steps%128==0:
   j={k:len(cls(c.S/'journals'/(k+'.jsonl')).snapshot()) for k,cls in [('birth',c.BirthJournal),('migration',c.MigrationJournal)]}
   print(json.dumps({'phase':state['phase'],'rows':state['rows'],'verified':state['verified_rows'],'admissions':j,'seconds':round(time.monotonic()-start,1)}),flush=True)
   if max(j.values())>=448:raise RuntimeError('ADMISSION_PRESSURE')
  if state['phase']=='COMPLETE':break
 result={'status':'PASS_FENCED_CAPACITY_AND_COVERAGE','source_count':state['rows'],'target_count':state['verified_rows'],'digest':state['digest'],'checkpoint':str(cp),'target_bytes':(base/'ops.sqlite').stat().st_size,'seconds':time.monotonic()-start,'free_bytes':shutil.disk_usage(c.S).free}
 c.save(c.S/'checkpoints/quarantine-capacity-result.json',result);print(json.dumps(result,indent=2),flush=True)
except Exception as e:
 c.save(c.S/'checkpoints/quarantine-capacity-result.json',{'status':'HOLD','error':str(e),'steps':steps});print('HOLD',str(e),flush=True);raise
