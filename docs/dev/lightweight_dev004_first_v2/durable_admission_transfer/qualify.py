"""Actual retained file-journal helpers, disposable destinations only."""
import ast,hashlib,json,os,resource,socket,tempfile
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
socket.create_connection=socket.socket.connect
p=Path('/Users/kevinkeaveney/Production/flex-listener/src/core/pumpfun_curve_listener.py');raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest();assert sha=='751d40d14150b112513cfbed44329cf1c9f0e3e58f34369dd8f29550a1cd3a5f'
tree=ast.parse(raw);out=Path(tempfile.mkdtemp(prefix='dev004-transfer-authority-',dir='/private/tmp'));scope={'os':os,'DB_PATH':str(out/'core-v1.sqlite')}
for name in ['_fallback_file_path','_migration_fallback_file_path','_durable_jsonl_append','_migration_fallback_append','_fallback_append_birth']:
 n=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),str(p),'exec'),scope)
original=os.fsync;sync=[]
def tracked(fd):sync.append(os.fstat(fd).st_mode);return original(fd)
os.fsync=tracked
try:
 scope['_fallback_append_birth']('synthetic-mint','synthetic-creator','1',None,'synthetic-create',None,None,1,'FENCE',birth_evidence_source='PUMPPORTAL_NEW_TOKEN')
 birth_syncs=len(sync);birthpath=Path(scope['_fallback_file_path']());birth=json.loads(birthpath.read_text())
 scope['_migration_fallback_append']('synthetic-mint','synthetic-migration',1,'mark_migrated','FENCE')
 migration_syncs=len(sync)-birth_syncs;migrationpath=Path(scope['_migration_fallback_file_path']());migration=json.loads(migrationpath.read_text())
 scope['_migration_fallback_append']('synthetic-mint','synthetic-migration',1,'mark_migrated','FENCE')
 duplicate_lines=len(migrationpath.read_text().splitlines())
 scope['DB_PATH']=str(out/'core-v2.sqlite')
 checks={'existing_file_stores_write_without_Core_or_OPS_connection':not (out/'core-v1.sqlite').exists() and birth['create_tx_signature']=='synthetic-create' and migration['signature']=='synthetic-migration','birth_helper_has_no_fsync_publication':birth_syncs==0,'migration_helper_fsyncs_file':migration_syncs==1,'journal_append_does_not_deduplicate_migrations':duplicate_lines==2,'DB_rebind_changes_journal_lookup_and_leaves_V1_records_behind':Path(scope['_migration_fallback_file_path']())!=migrationpath and not Path(scope['_migration_fallback_file_path']()).exists() and migrationpath.exists()}
 assert all(checks.values())
finally:os.fsync=original
r={'status':'HOLD_NO_COMPLETE_INDEPENDENT_ADMISSION_AUTHORITY','source_sha256':sha,'checks':checks,'scope':'Actual file helpers only. File reopen is not power-loss certification. No DB/provider calls; no admission-hook or transfer implementation. Migration helper file fsync does not certify parent-directory publication or total file bounds.','maximum_file_bytes':max(p.stat().st_size for p in out.iterdir()),'production_writes':0,'provider_calls':0}
(out/'results.json').write_text(json.dumps(r,indent=2)+'\n');print(out);print(json.dumps(r,indent=2))
