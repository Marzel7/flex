from pathlib import Path
import subprocess,json,shutil,hashlib
D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');O=Path('/private/tmp/dev004-runtime-composition');pair=O/'shared-session';pair.mkdir(exist_ok=True)
for k in ['core','ops']:shutil.copy2('/private/tmp/dev004-compact-creator-qualified/classification_closure/'+k+'_v2.sqlite',pair/(k+'.sqlite'))
results=[]
for restart in [0,1]:
 for package,module in [('listener','src.core.pumpfun_curve_listener'),('creator_resolution','src.core.creator_resolution_worker'),('walkback','src.core.walkback_worker'),('walkback','src.ops.attribution_outcome'),('creator_resolution','src.ops.living_potential_operations'),('creator_resolution','src.core.price_service'),('creator_resolution','src.ops.operation_monitor_service'),('creator_resolution','src.apis.price_api'),('creator_resolution','src.core.internal_api')]:
  r=subprocess.run(['/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python','-B','/private/tmp/dev004-runtime-probe.py',str(D/'runtime'/package),module,str(pair)],cwd=pair,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
  assert r.returncode==0,r.stderr
  x=json.load(open(pair/'probe.json'));assert x['status']=='IMPORT_PASS',x
  x['restart']=restart;results.append(x)
(O/'shared_results.json').write_text(json.dumps({'status':'IMPORT_RESTART_PASS','results':results,'same_database_pair':str(pair),'real_provider_calls':0,'full_startup_status':'HOLD_SEPARATE_BOOTSTRAP_BOUNDARIES'},indent=2)+'\n');print('PASS18 imports overtwo fresh-process rounds;same fixturepair;fullstartupHOLD')
