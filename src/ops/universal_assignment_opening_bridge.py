"""Generic durable canonical-assignment -> QualifiedOpening bridge."""
from __future__ import annotations
import hashlib,json,sqlite3
from src.ops.byzantine_forward_opening_adapter import EXECUTABLE_ADAPTERS as BYZANTINE_EXECUTABLE_ADAPTERS

REGISTRY={'watchtower':('FIRST_AVAILABLE','WATCHTOWER_FIRST_AVAILABLE_V1'),'byzantine':('BYZANTINE_ACTUAL_ENTRY_V2','BYZANTINE_ACTUAL_ENTRY_V2')}
EXECUTABLE_ADAPTERS = dict(BYZANTINE_EXECUTABLE_ADAPTERS)
def ident(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ensure_schema(db):
 db.execute('PRAGMA max_page_count=16384')
 db.executescript('''CREATE TABLE IF NOT EXISTS universal_opening_work(work_id TEXT PRIMARY KEY,assignment_id TEXT NOT NULL,operation_id TEXT NOT NULL,mint TEXT NOT NULL,adapter TEXT NOT NULL,state TEXT NOT NULL,opening_json TEXT,UNIQUE(assignment_id,adapter));
 CREATE TABLE IF NOT EXISTS universal_opening_visible(assignment_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,mint TEXT NOT NULL,state TEXT NOT NULL);''');db.commit()
def trigger(db,assignment):
 ensure_schema(db); op=assignment['operation_id']; adapter=REGISTRY.get(op)
 if not adapter:
  with db: db.execute('INSERT OR IGNORE INTO universal_opening_visible VALUES(?,?,?,?)',(assignment['id'],op,assignment['mint'],'ADAPTER_UNAVAILABLE'))
  return {'status':'ADAPTER_UNAVAILABLE'}
 name,version=adapter; work=ident({'assignment_id':assignment['id'],'adapter':version})
 with db:
  db.execute('INSERT OR IGNORE INTO universal_opening_work VALUES(?,?,?,?,?,?,?)',(work,assignment['id'],op,assignment['mint'],name,'PENDING',None))
  db.execute('INSERT OR IGNORE INTO universal_opening_visible VALUES(?,?,?,?)',(assignment['id'],op,assignment['mint'],'OPENING_PENDING'))
 return {'status':'OPENING_WORK_READY','work_id':work,'adapter':name}
def commit_qualified(db,work_id,opening,universal_handoff):
 row=db.execute('SELECT assignment_id,operation_id,mint,adapter,state FROM universal_opening_work WHERE work_id=?',(work_id,)).fetchone()
 if not row: return {'status':'WORK_NOT_FOUND'}
 if row[4]=='QUALIFIED': return {'status':'ALREADY_QUALIFIED'}
 if not opening.get('qualified') or opening.get('timestamp') is None or opening.get('mc_usd') is None: return {'status':'ENTRY_REQUIRED'}
 # durable Opening commit precedes injected handoff; no provider is called here.
 with db: db.execute('UPDATE universal_opening_work SET state=?,opening_json=? WHERE work_id=?',('QUALIFIED',json.dumps(opening,sort_keys=True),work_id)); db.execute('UPDATE universal_opening_visible SET state=? WHERE assignment_id=?',('LIVE',row[0]))
 universal_handoff({'id':row[0],'operation_id':row[1],'mint':row[2]},opening)
 return {'status':'QUALIFIED_AND_HANDED_OFF','adapter':row[3]}
