import subprocess,sqlite3,json,os
from pathlib import Path
O=Path('/private/tmp/dev004-walkback-crash-qualification');O.mkdir(exist_ok=False);out=[]
for phase in ['before','after']:
 root=O/phase;root.mkdir();r=subprocess.run(['python3','/private/tmp/dev004-walkback-crash-child.py',str(root),phase,phase],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=30);assert r.returncode==(71 if phase=='before' else 72),(phase,r.returncode,r.stderr)
 c=sqlite3.connect(root/'ops.sqlite');c.row_factory=sqlite3.Row;row=c.execute("select * from wt_walkback_queue where creator like '%' order by rowid desc limit 1").fetchone();mint=row['mint'];first={'queue_status':row['status'],'outcome_count':c.execute('select count(*) from wt_attribution_outcomes where mint=?',(mint,)).fetchone()[0],'membership_count':c.execute('select count(*) from operator_launch_membership where mint=?',(mint,)).fetchone()[0]}
 if phase=='before':c.execute('update wt_walkback_queue set lease_expires_at=0 where mint=?',(mint,));c.commit()
 c.close();restart=subprocess.run(['python3','/private/tmp/dev004-walkback-crash-child.py',str(root),phase,'off'],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=30);assert restart.returncode==0,(restart.stdout,restart.stderr);second=json.load(open(root/'child_off.json'));out.append({'phase':phase,'actual_process_exit':r.returncode,'before_restart':first,'after_restart':second,'stdout_restart':restart.stdout})
 if phase=='before':assert second['state']['queue']['status']=='complete' and second['state']['memberships']==1
 else:assert second['state']['queue']['status']=='complete' and second['state']['memberships']==0
(O/'result.json').write_text(json.dumps({'status':'HOLD_POSTCOMMIT_PROJECTION_RECOVERY','cases':out},indent=2)+'\n');print(json.dumps({'status':'HOLD_POSTCOMMIT_PROJECTION_RECOVERY','before_recovered_membership':out[0]['after_restart']['state']['memberships'],'after_commit_restart_membership':out[1]['after_restart']['state']['memberships']},indent=2))
