"""Committed materialization to semantic-input bridge; never reads raw websocket events."""
import hashlib,json,sqlite3,time
from pathlib import Path
from src.ops.pumpswap_prospective_semantic_capture import enqueue_committed
from src.ops.pumpswap_pool_identity_binding import bind
VERSION='pumpswap-materialized-semantic-v1';CONSUMER='pumpswap-materialized-semantic-transformer-v1'
def _c(p):Path(p).parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(p);c.row_factory=sqlite3.Row;return c
def ensure(p):
 with _c(p) as c:
  c.execute('CREATE TABLE IF NOT EXISTS pumpswap_semantic_transforms(id TEXT PRIMARY KEY,materialized_id TEXT UNIQUE NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,created_at INTEGER NOT NULL)')
def transform_all(materialized_db,semantic_db,*,env=None):
 ensure(semantic_db);count=0;eligible=0
 with _c(materialized_db) as c:rows=c.execute("SELECT id,payload FROM pumpswap_materializations WHERE status='PUMPSWAP_INSTRUCTIONS_MATERIALIZED' ORDER BY created_at,id").fetchall()
 for row in rows:
  p=json.loads(row['payload']);p['id']=row['id'];binding=bind(p);ident=hashlib.sha256(f"{row['id']}\0{VERSION}".encode()).hexdigest()
  # Pool identity is deliberately required; arbitrary ordered accounts are not a pool proof.
  pool=binding.get('pool');record={'materialized_instruction_id':row['id'],'raw_event_id':p['raw_event_id'],'transaction_evidence_id':p.get('transaction_evidence_id'),'signature':p['signature'],'slot':p.get('slot'),'instruction_index':(p.get('instructions')or[{}])[0].get('outer_instruction_index',-1),'pool':pool,'mint':binding.get('mint'),'pool_binding':binding,'instruction_bytes':(p.get('instructions')or[{}])[0].get('instruction_data'),'ordered_accounts':(p.get('instructions')or[{}])[0].get('accounts',[]),'transaction_boundary':True,'fee_evidence':'FEE_ATTRIBUTION_UNRESOLVED','reserve_evidence':'RESERVE_STATE_UNRESOLVED'}
  status='ELIGIBLE' if pool and record['instruction_index']>=0 else 'BOUNDARY_INSUFFICIENT'
  with _c(semantic_db) as c:c.execute('INSERT OR REPLACE INTO pumpswap_semantic_transforms VALUES (?,?,?,?,?)',(ident,row['id'],status,json.dumps(record,sort_keys=True),int(time.time())))
  count+=1
  if status=='ELIGIBLE':enqueue_committed(semantic_db,record,committed=True,env=env);eligible+=1
 return {'transformed':count,'eligible':eligible}
def submission_capability():return 'NONE'
