import ast,importlib.util,os,sys,types,socket,resource,tempfile
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));sys.dont_write_bytecode=True
root=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');out=Path(tempfile.mkdtemp(prefix='dev004-existing-writer-',dir='/private/tmp'))
os.environ.clear();os.environ.update({'PATH':'/usr/bin:/bin','DB_PATH':str(out/'core.sqlite'),'WT_OPS_DB_PATH':str(out/'ops.sqlite'),'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONDONTWRITEBYTECODE':'1'})
socket.socket.connect=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'));socket.create_connection=socket.socket.connect
for name in ['src','src.core','src.utils']:
 p=types.ModuleType(name);p.__path__=[];sys.modules[name]=p
for name,rel in [('src.core.database_write_service','src/core/database_write_service.py'),('src.utils.db_locking','src/utils/db_locking.py')]:
 path=root/'runtime/listener'/rel;spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m
 tree=ast.parse(path.read_text());tree.body=[n for n in tree.body if not(isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='_start_connection_reaper')];exec(compile(tree,str(path),'exec'),m.__dict__)
 if name.startswith('src.utils'):sys.modules['src.utils'].db_locking=m
# Audit covers native SQLite as well as Python wrappers.
def audit(event,args):
 if event=='sqlite3.connect':
  raw=str(args[0]);plain=raw[5:].split('?',1)[0] if raw.startswith('file:') else raw
  if plain!=':memory:' and not Path(plain).resolve().is_relative_to(out):raise RuntimeError('OUTSIDE_DISPOSABLE_DB')
sys.addaudithook(audit)
import pytest
print('OUTPUT_DIRECTORY='+str(out),flush=True)
raise SystemExit(pytest.main(['--noconftest','--import-mode=importlib','-q',str(root/'tests/test_database_write_service.py'),str(root/'tests/test_x78_13_autocommit_write_lane.py'),str(root/'tests/test_x78_20_write_lane_priority.py'),'--basetemp='+str(out/'fixtures'),'-o','cache_dir='+str(out/'cache'),'--junitxml='+str(out/'results.xml')]))
