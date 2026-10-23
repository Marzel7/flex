from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolated-bootstrap-setup','exec'))
from src.ops import operator_writer,living_potential_operations,generic_living_lineage_metadata
# Only the existing explicitly requested shared-writer operation may start a writer.
previous_start=threading.Thread.start

def accepted_writer_start(self,*a,**kw):
 if self.name.startswith('db-writer-operations:') and str(out) in self.name:return original_start(self,*a,**kw)
 return previous_start(self,*a,**kw)
threading.Thread.start=accepted_writer_start
c=sqlite3.connect(str(out/'ops.sqlite'));c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on');c.execute('pragma wal_autocheckpoint=0')
result={'owners':[]}
try:
 operator_writer.initialize_operator_schema(str(out/'ops.sqlite'));result['owners'].append('OperatorWriter.initialize_schema')
 living_potential_operations.ensure_schema(c);result['owners'].append('living_potential_operations.ensure_schema')
 generic_living_lineage_metadata.ensure_lineage_schema(c);c.commit();result['owners'].append('generic_living_lineage_metadata.ensure_lineage_schema')
 before=[tuple(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')]
 operator_writer.initialize_operator_schema(str(out/'ops.sqlite'));living_potential_operations.ensure_schema(c);generic_living_lineage_metadata.ensure_lineage_schema(c);c.commit()
 after=[tuple(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')];assert before==after
 result.update(status='PASS',idempotent_schema=True,foreign_key_check=[tuple(r) for r in c.execute('pragma foreign_key_check')],objects=[{'type':r[0],'name':r[1],'table':r[2]} for r in after if not r[1].startswith('sqlite_')],database_attempts=db_attempts)
except BaseException as e:result.update(status='HOLD',error=str(e)[:500],traceback=traceback.format_exc(limit=6),database_attempts=db_attempts)
c.close();result['sizes']={p.name:p.stat().st_size for p in out.iterdir() if p.is_file() and '.sqlite' in p.name};(out/'ops-supplement.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k in ['status','error','idempotent_schema','foreign_key_check','sizes']}))
