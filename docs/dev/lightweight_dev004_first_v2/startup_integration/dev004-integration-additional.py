from pathlib import Path
s=Path('/private/tmp/dev004-schema-bootstrap-child.py').read_text();exec(compile(s.split('results=[]')[0],'isolation','exec'))
from src.ops import living_potential_operations,generic_living_lineage_metadata as lineage
c=sqlite3.connect(str(out/'ops.sqlite'));living_potential_operations.ensure_schema(c);c.commit()
lineage.ensure_lineage_schema(c);before=c.execute('select type,name,sql from sqlite_schema order by name').fetchall();lineage.ensure_lineage_schema(c);assert before==c.execute('select type,name,sql from sqlite_schema order by name').fetchall();c.close()
print('PASS Potential Operations and repeated lineage initialization')
