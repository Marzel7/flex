"""Replay precondition of existing migration journal; not a new admission owner."""
import ast,builtins,hashlib,json,os,resource,socket,sqlite3,tempfile,time,types
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
socket.create_connection=socket.socket.connect
source=Path('/Users/kevinkeaveney/Production/flex-listener/src/core/pumpfun_curve_listener.py');raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest();assert sha=='751d40d14150b112513cfbed44329cf1c9f0e3e58f34369dd8f29550a1cd3a5f';tree=ast.parse(raw)
out=Path(tempfile.mkdtemp(prefix='dev004-journal-replay-gate-',dir='/private/tmp'));db=out/'core.sqlite'
ddl=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'CREATE TABLE IF NOT EXISTS migration_persist_queue (' in n.value)
with sqlite3.connect(db) as c:c.execute('PRAGMA max_page_count=256');c.execute(ddl)
def connect(path,**kwargs):
 assert Path(path).resolve()==db.resolve();kwargs.pop('priority',None);return sqlite3.connect(path,**kwargs)
def imp(name,*a,**kw):return types.SimpleNamespace(connect=connect) if name=='sqlite3' else builtins.__import__(name,*a,**kw)
scope={'__builtins__':dict(vars(builtins),__import__=imp),'os':os,'time':time,'DB_PATH':str(db),'PRIORITY_P0_CRITICAL_INGESTION':0}
for name in ['_migration_fallback_file_path','_durable_jsonl_append','_migration_fallback_append','_reconcile_migration_fallback_file_into_queue']:
 n=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),str(source),'exec'),scope)
path=Path(scope['_migration_fallback_file_path']())
def append(mint,sig):scope['_migration_fallback_append'](mint,sig,1,'synthetic-pre-dispatch-probe','TEST')
append('known-mint','known-signature');valid=scope['_reconcile_migration_fallback_file_into_queue']()
with sqlite3.connect(db) as c:validrows=c.execute('SELECT signature,mint,status FROM migration_persist_queue').fetchall()
append(None,'not-yet-resolved-signature');unknown=scope['_reconcile_migration_fallback_file_into_queue']();unknown_retained=path.exists() and 'not-yet-resolved-signature' in path.read_text()
append('second-known-mint','second-known-signature');mixed=scope['_reconcile_migration_fallback_file_into_queue']();mixed_file_removed=not path.exists()
with sqlite3.connect(db) as c:rows=c.execute('SELECT signature,mint,status FROM migration_persist_queue ORDER BY id').fetchall()
path.write_text('{"version":');partial=scope['_reconcile_migration_fallback_file_into_queue']()
checks={'existing_resolved_migration_replay_works':valid==1 and validrows==[('known-signature','known-mint','PENDING')],'unresolved_predispatch_identity_not_importable':unknown==0 and unknown_retained,'mixed_resolved_unresolved_would_remove_untransferred_record':mixed==1 and mixed_file_removed and all(r[0]!='not-yet-resolved-signature' for r in rows),'partial_json_blocks_import_and_keeps_file':partial==0 and path.exists()}
assert all(checks.values())
r={'status':'HOLD_EXISTING_RECORD_IS_POST_RESOLUTION_RETRY_NOT_UNIVERSAL_ADMISSION','checks':checks,'scope':'Actual writer/importer and accepted queue DDL on disposable DB. Null mint models a proposed pre-dispatch notification, not normal accepted production journal use. No evidence that such records exist in production. Import-priority argument stripped only for standard disposable SQLite.','source_sha256':sha,'queue_rows':rows,'provider_calls':0,'production_writes':0,'maximum_fixture_file_bytes':max(p.stat().st_size for p in out.iterdir())}
(out/'results.json').write_text(json.dumps(r,indent=2)+'\n');print(out);print(json.dumps(r,indent=2))
