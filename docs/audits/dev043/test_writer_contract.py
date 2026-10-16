"""Compact DEV043 tests of the exact recovered writer; no operational imports."""
import sys,os,pathlib,sqlite3,resource,json
ROOT=pathlib.Path(__file__).resolve().parents[3]
STATE=pathlib.Path('/private/tmp/dev043-writer-fixtures');STATE.mkdir(exist_ok=True)
os.chdir(STATE);sys.path.insert(0,str(ROOT));resource.setrlimit(resource.RLIMIT_FSIZE,(8388608,8388608))
def audit(event,args):
 if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'):raise PermissionError(event)
 if event=='open' and not isinstance(args[0],int):
  mode=args[1] or ''; flags=args[2]
  if any(c in mode for c in 'wax+') or flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
   if not pathlib.Path(args[0]).resolve().is_relative_to(STATE):raise PermissionError(str(args[0]))
sys.addaudithook(audit)
from src.core.db_writer import commit_write_and_wait,DurableWriteReceipt
from src.core.db_write_queue import WriteItem,queue_depths
import src.core.db_writer as writer
p=STATE/'contract.db';c=sqlite3.connect(p);c.execute('CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY,value TEXT)');c.execute('DELETE FROM evidence');c.commit();c.close()
def item(statements):return WriteItem('enrichment','DEV043-fixture',statements,'')
r=commit_write_and_wait(str(p),item([('INSERT INTO evidence VALUES (?,?)',(1,'committed')),('INSERT INTO evidence VALUES (?,?)',(2,'atomic'))]));assert r.committed and r.commit_timestamp and r.statements_written==2
with sqlite3.connect(p) as c:assert c.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==2
r=commit_write_and_wait(str(p),item([('INSERT INTO evidence VALUES (?,?)',(3,'rollback')),('INSERT INTO evidence VALUES (?,?)',(1,'duplicate'))]));assert not r.committed and r.error=='SHARED_WRITER_COMMIT_FAILED'
with sqlite3.connect(p) as c:assert c.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==2
r=commit_write_and_wait(str(STATE/'absent.db'),item([]));assert not r.committed and r.error=='WRITER_DATABASE_UNAVAILABLE'
original=writer.database_write_service.submit
try:
 def timeout(*a,**kw):raise TimeoutError('fixture timeout')
 writer.database_write_service.submit=timeout
 r=commit_write_and_wait(str(p),item([('INSERT INTO evidence VALUES (?,?)',(4,'timeout'))]));assert not r.committed and r.commit_timestamp is None and r.error=='SHARED_WRITER_COMMIT_FAILED'
finally:writer.database_write_service.submit=original
assert not any(queue_depths().values())
print(json.dumps({'status':'PASS','checks':['compatible imports','post-commit independent readback','multi-statement atomic rollback','missing database failure receipt','service timeout converted to failure receipt','no best-effort queue use'],'fixture_max_bytes':max(f.stat().st_size for f in STATE.iterdir() if f.is_file()),'provider_calls':0,'canonical_writes':0}))
