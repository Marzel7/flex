from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolated-owner-setup','exec'))
import src.core.pumpfun_curve_listener as listener
result={'status':'HOLD_BASE_SCHEMA','side_contracts':{}}
try:
 listener._ensure_webhook_birth_queue_schema(str(out/'core.sqlite'))
 c=sqlite3.connect(str(out/'core.sqlite'));c.row_factory=sqlite3.Row
 first=[tuple(r) for r in c.execute("select type,name,tbl_name,sql from sqlite_schema where tbl_name='webhook_birth_queue' order by name")];c.close()
 listener._ensure_webhook_birth_queue_schema(str(out/'core.sqlite'))
 c=sqlite3.connect(str(out/'core.sqlite'));c.row_factory=sqlite3.Row
 second=[tuple(r) for r in c.execute("select type,name,tbl_name,sql from sqlite_schema where tbl_name='webhook_birth_queue' order by name")];assert first==second
 result['side_contracts']['webhook_birth_retry_schema']='PASS_IDEMPOTENT_EXISTING_OWNER'
 try:c.execute('EXPLAIN SELECT pf_ws_creator,earliest_tx_creator,create_tx_signature,migrated_at FROM token_analysis WHERE mint=?',('DEV004_SYNTHETIC',));result['canonical_create_read_plan']='PASS'
 except sqlite3.Error as e:result['canonical_create_read_plan']={'status':'HOLD','error':str(e)}
 result['foreign_key_check']=[tuple(r) for r in c.execute('pragma foreign_key_check')]
 result['all_user_rows']={r[0]:c.execute('select count(*) from "'+r[0]+'"').fetchone()[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()}
 result['objects']=[tuple(r) for r in c.execute("select type,name,tbl_name from sqlite_schema where name not like 'sqlite_%' order by type,name")]
 c.close();result['core_bytes']=(out/'core.sqlite').stat().st_size
except BaseException as e:result.update(error=str(e),traceback=traceback.format_exc(limit=5))
result['database_attempts']=db_attempts;result['blocked']=blocked
(out/'final_check.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k in ['status','side_contracts','canonical_create_read_plan','foreign_key_check','core_bytes','error']}))
