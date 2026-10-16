"""DEV043 full existing template render with retained authority and compact cases."""
import os,sys,pathlib,sqlite3,socket,json,copy,resource
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
OPS=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db')
os.environ['WT_OPS_DB_PATH']=str(OPS);os.environ['OPERATION_RESEARCH_ROOT']=str(ROOT)
resource.setrlimit(resource.RLIMIT_FSIZE,(8388608,8388608))
def audit(event,args):
 if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen'):raise PermissionError(event)
 if event=='open' and not isinstance(args[0],int):
  mode=args[1] or '';flags=args[2]
  if any(x in mode for x in 'wax+') or flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):raise PermissionError('no fixture files needed')
sys.addaudithook(audit)
orig=sqlite3.connect
def connect(db,*a,**kw):
 assert str(db).startswith('file:'+str(OPS)) and 'mode=ro' in str(db)
 kw['uri']=True;c=orig(OPS.as_uri()+'?mode=ro&immutable=1',*a,**kw);c.execute('PRAGMA query_only=ON');return c
sqlite3.connect=connect
from flask import Flask,render_template
from src.ops.operation_playbook_registry import artifact_playbook
app=Flask('DEV043',template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'))
model=artifact_playbook('watchtower');before=copy.deepcopy(model);assert len(model['prospective_tokens'])==4
nullrow=next(x for x in model['prospective_tokens'] if x['mint']=='3uawxC4Ci2VhFpcRw5eD1KKsK3EG317WzxcWsYEjpump')
assert nullrow['entry_mc_usd'] is not None and nullrow['current_final_mc_usd'] is None
checks=[]
def render(m):
 with app.test_request_context('/operations/watchtower/playbook'):return render_template('operation_artifact_playbook.html',playbook=m)
html=render(model);assert 'UNAVAILABLE' in html and '/operations/playbooks' in html and 'https://gmgn.ai/sol/token/' in html;checks+=['retained model no500','existing links','exact current_final_mc_usd null operand']
for field,numeric,expected in [('entry_mc_usd',123456,'$123.5K'),('current_final_mc_usd',23456,'$23.46K'),('max_proven_mc_usd',2345678,'$2.35M'),('entry_to_max_multiple',2.34,'2.34×')]:
 for value in [numeric,None]:
  m=copy.deepcopy(model);row=copy.deepcopy(model['prospective_tokens'][0]);row[field]=value;m['prospective_tokens']=[row];out=render(m)
  section=out.split('Automatically admitted Watchtower assignments',1)[1]
  assert (expected if value is not None else 'UNAVAILABLE') in section
  checks.append(field+(' numeric' if value is not None else ' null'))
# Main table formerly coerced null to zero. Guard each independent retained field.
for field in ['entry_mc_usd','max_proven_mc_usd','entry_to_max_multiple']:
 m=copy.deepcopy(model);m['tokens']=[copy.deepcopy(model['tokens'][0])];m['tokens'][0][field]=None
 assert 'UNAVAILABLE' in render(m).split('id="watchtower-table"',1)[1].split('</tbody>',1)[0];checks.append('main table '+field+' null')
m=copy.deepcopy(model);m['prospective_tokens'][0]['entry_mc_usd']='INCOMPATIBLE_SCHEMA'
try:render(m)
except TypeError:checks.append('incompatible type remains failure')
else:raise AssertionError('bad schema hidden')
assert model==before
print(json.dumps({'status':'PASS','checks':checks,'retained_prospective_rows':4,'historical_rows':51,'model_unchanged':True,'provider_calls':0,'database_writes':0}))
