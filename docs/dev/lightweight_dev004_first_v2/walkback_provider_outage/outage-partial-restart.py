from pathlib import Path
import subprocess,os,json
results=[]
for first,code in [('outage_at_2',79),('outage_at_4',79),('outage_at_8',79)]:
 root=Path('/private/tmp/dev004-outage-restart-v3-'+first);env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OUTAGE_TEST_ROOT':str(root)}
 for phase,expected in [(first,code),('recover',0),('replay',0)]:
  r=subprocess.run(['python3','/private/tmp/outage-restart-child.py'],env={**env,'OUTAGE_PHASE':phase},capture_output=True,text=True,timeout=30)
  assert r.returncode==expected,(phase,r.stdout,r.stderr)
 result=json.load(open(root/'happy.json'));assert result['queue']['status']=='complete' and len(result['membership'])==1 and result['outcome']['confidence']=='HIGH'
 results.append({'case':first,'status':'PASS','restart_confirmed_attribution':True,'memberships':1,'successful_requests':len(result['synthetic_calls']),'attempts':result['queue']['attempts']})
Path('/private/tmp/dev004-provider-outage-final/partial_request_results.json').write_text(json.dumps(results,indent=2)+'\n');print(results)
