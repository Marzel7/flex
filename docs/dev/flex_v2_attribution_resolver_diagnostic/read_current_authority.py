"""Bounded read-only V2 authority check. No provider transports or mutations.

Run with the existing algotrader Python. Emits aggregate diagnostics only.
Does not initialize schemas, call lifecycle methods, acquire the gate lock,
restore provider access, or control services. Canonical/OPS values are not fixed
across natural processing; output is an observed snapshot, not atomic cross-DB.
"""
import configparser,contextlib,datetime,hashlib,importlib.util,json,os,pathlib,re,resource,shutil,socket,sqlite3,subprocess,sys,time
sys.dont_write_bytecode=True
resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,499000000))
def forbidden(*a,**k):raise RuntimeError('DIAGNOSTIC_NETWORK_FORBIDDEN')
socket.socket.connect=forbidden;socket.create_connection=forbidden
S=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2')
CONF=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex-development/DEV090/operational-restoration/supervisord.selective-retry2.conf')
FLOOR=2067108864

def ro(path):
 c=sqlite3.dbapi2.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def main():
 if shutil.disk_usage(S).free<FLOOR:raise RuntimeError('DISK_FLOOR')
 plan=json.load(open(S/'authority/frozen-runtime-plan.json'));cfg=configparser.ConfigParser(interpolation=None);cfg.read(CONF)
 from supervisor.datatypes import dict_of_key_value_pairs
 roles={'watchtower_listener':'listener','creator_resolution_worker':'creator_resolution','walkback_worker':'walkback'};bindings={};sources={}
 for name,role in roles.items():
  env=dict_of_key_value_pairs(cfg['program:'+name]['environment']);assert env['DB_PATH']==str(S/'databases/core.sqlite') and env['WT_OPS_DB_PATH']==str(S/'databases/ops.sqlite') and env['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'
  os.environ.update(env);root=pathlib.Path(plan['roles'][role]['package']);p=root/'src/core/paired_binding_guard.py';spec=importlib.util.spec_from_file_location('diagnostic_pair_'+role,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);bindings[role]=m.validate_pair()
  filename='pumpfun_curve_listener.py' if role=='listener' else 'creator_resolution_queue.py' if role=='creator_resolution' else 'walkback_worker.py'
  sources[role]={f:hashlib.sha256((root/'src/core'/f).read_bytes()).hexdigest() for f in [filename,'helius_denial_gate.py']}
 assert len(set(bindings.values()))==1
 gatepath=pathlib.Path(plan['roles']['listener']['package'])/'src/core/helius_denial_gate.py';spec=importlib.util.spec_from_file_location('diagnostic_only_gate_reader',gatepath);gm=importlib.util.module_from_spec(spec);spec.loader.exec_module(gm);g=gm.Gate(S/'journals/helius-denial-v1.json',next(iter(bindings.values())));state=None;failure=None
 for i in range(5):
  try:state=g.read();break
  except gm.Integrity as exc:
   failure=str(exc)
   if failure!='HELIUS_GATE_PUBLICATION_AMBIGUOUS':break
   time.sleep(.1)
 gate={'classification':'BLOCKED','read_error':failure}
 if state is not None:
  b=state['rpc_budget'];gate={'classification':'VERIFIED_SNAPSHOT','state':state['state'],'limit':b['policy']['lifetime_credits'],'clock_divergence_seconds':abs((int(time.time())-b['wall'])-(int(time.monotonic())-b['mono'])),'inflight':[{'role':r['role'],'age_seconds':int(time.time())-b['keys'][r['key']]['at']} for r in b['inflight'].values()],'highest_investigation_spend':max(r['credits'] for r in b['scopes'].values()),'total_reserved_credits':sum(r['credits'] for r in b['scopes'].values()),'unique_http_completed_request_keys_updated_since_window':sum(r['status']=='HTTP_COMPLETED' and r['at']>=1793178661 for r in b['keys'].values())}
 marker=(S/'journals/helius-denial-v1.json.lock').read_bytes();gate['marker_at_read_end']='READY' if marker.startswith(b'READY_V1 ') else 'PENDING' if marker.startswith(b'PENDING_V1') else 'UNKNOWN'
 with contextlib.closing(ro(S/'databases/ops.sqlite')) as c:
  queue=dict(c.execute('SELECT status,COUNT(*) FROM wt_walkback_queue GROUP BY status'));hb=c.execute("SELECT last_seen,status FROM wt_worker_heartbeat WHERE worker_name='walkback_worker'").fetchone();pending=[dict(r) for r in c.execute("SELECT last_error,attempts FROM wt_walkback_queue INDEXED BY ix_wbq_status WHERE status='pending' LIMIT 10001")];assert len(pending)<10001
 with contextlib.closing(ro(S/'databases/core.sqlite')) as c:
  resolver=[dict(r) for r in c.execute('SELECT status,next_attempt_at,locked_until,created_at,resolved_at FROM creator_resolution_queue INDEXED BY idx_creator_resolution_queue_status LIMIT 10001')];assert len(resolver)<10001
  control=c.execute('SELECT enabled,pending_count,ack_count,admitted FROM wb_create_control WHERE id=1').fetchone()
 out=subprocess.check_output(['/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl','-c',str(CONF),'status',*roles],text=True);services={}
 for line in out.splitlines():
  t=line.split();pid=re.search(r'pid (\d+)',line);services[t[0]]={'status':t[1],'pid':int(pid.group(1)) if pid else None}
 result={'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'bindings':bindings,'source_hashes':sources,'gate':gate,'queue_states':queue,'heartbeat':{'last_seen':hb['last_seen'],'status':hb['status']},'pending_deferral_reasons':dict(__import__('collections').Counter(r['last_error'] for r in pending)),'resolver_states':dict(__import__('collections').Counter(r['status'] for r in resolver)),'resolver_due_eligible':sum(r['status'] in ('pending','retry') and r['locked_until']<time.time() and r['next_attempt_at']<=time.time() for r in resolver),'create_control':dict(control),'services':services,'free_bytes':shutil.disk_usage(S).free,'provider_calls':0,'production_changes':0,'service_actions':0}
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
