"""Small SQLite semantics controls; no application/network startup or live DB access."""
import hashlib,json,os,resource,socket,sqlite3,tempfile
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
socket.create_connection=socket.socket.connect
out=Path(tempfile.mkdtemp(prefix='dev004-continuous-cutover-',dir='/private/tmp'))
def create(name,label):
 p=out/name
 c=sqlite3.connect(p);c.execute('PRAGMA max_page_count=256');c.execute('CREATE TABLE generation(value TEXT)');c.execute('INSERT INTO generation VALUES (?)',(label,));c.commit();c.close();return p
core=create('core.sqlite','V1');newcore=create('core-new.sqlite','V2');ops=create('ops.sqlite','V1');newops=create('ops-new.sqlite','V2')
old=sqlite3.connect(f'file:{core}?mode=ro',uri=True)
assert old.execute('SELECT value FROM generation').fetchone()[0]=='V1'
os.replace(newcore,core)
fresh=sqlite3.connect(f'file:{core}?mode=ro',uri=True)
old_label=old.execute('SELECT value FROM generation').fetchone()[0];new_label=fresh.execute('SELECT value FROM generation').fetchone()[0]
o=sqlite3.connect(f'file:{ops}?mode=ro',uri=True);ops_label=o.execute('SELECT value FROM generation').fetchone()[0]
checks={'existing_connection_keeps_V1_after_path_replace':old_label=='V1' and new_label=='V2','separate_core_ops_replacements_have_mixed_boundary':new_label=='V2' and ops_label=='V1'}
old.close();fresh.close();o.close()
# Small write measurement only, not a cutover/WAL worst-case claim.
w=sqlite3.connect(out/'wal-probe.sqlite');w.execute('PRAGMA max_page_count=256');w.execute('PRAGMA journal_mode=WAL');w.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, payload TEXT)');w.commit()
for i in range(100):w.execute('INSERT INTO events VALUES (?,?)',(i,'synthetic'))
w.commit()
measured={p.name:p.stat().st_size for p in out.iterdir() if p.is_file()}
checks['fixture_under_file_limit']=max(measured.values())<16000000
w.close()
result={'checks':checks,'scope':'SQLite path/connection semantics only; not actual service hot-switch, queue replay or cutover acceptance','fixture_file_bytes_before_close':measured,'maximum_file_bytes':max(measured.values()),'provider_calls':0,'production_database_connections':0,'production_writes':0}
assert all(checks.values())
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(str(out));print(json.dumps(result,indent=2))
