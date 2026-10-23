from pathlib import Path
import sqlite3,sys,types,os,resource,socket,urllib.request,shutil,importlib,json
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));O=Path('/private/tmp/dev004-walkback-e2e-qualified');O.mkdir(exist_ok=True);S=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');W=Path('/Users/kevinkeaveney/Production/flex-walkback-dev002');D=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex')
for k in ['core','ops']:
 if not (O/(k+'.sqlite')).exists():shutil.copy2(S/(k+'_v2.sqlite'),O/(k+'.sqlite'))
os.environ.update(DB_PATH=str(O/'core.sqlite'),WT_OPS_DB_PATH=str(O/'ops.sqlite'),HELIUS_RPC_URL='http://invalid.synthetic',DEV004_CREATOR_HISTORY_PATH='/private/tmp/dev004-compact-creator-qualified/history.json',PYTHONDONTWRITEBYTECODE='1')
def forbidden(*a,**k):raise AssertionError('REAL_NETWORK_FORBIDDEN')
socket.socket.connect=forbidden;urllib.request.urlopen=forbidden
raw=sqlite3.connect

def connect(path,*a,**kw):
 text=str(path);p=Path(text[5:].split('?')[0] if text.startswith('file:') else text).resolve();assert text==':memory:' or p.is_relative_to(O),'NON_FIXTURE_CONNECTION: '+str(p)
 c=raw(path,*a,**kw)
 if 'mode=ro' not in text:c.execute('pragma max_page_count=3840')
 return c
sqlite3.connect=connect
for name,sub in [('src','src'),('src.core','src/core'),('src.ops','src/ops'),('src.utils','src/utils')]:m=types.ModuleType(name);m.__path__=[str(W/sub)];sys.modules[name]=m
ww=importlib.import_module('src.core.walkback_worker');q=importlib.import_module('src.core.walkback_queue');ao=importlib.import_module('src.ops.attribution_outcome');print('BOOTSTRAP_OK',ww.__file__)
ops=connect(O/'ops.sqlite');ops.row_factory=sqlite3.Row
print('schema before',len(ops.execute('pragma table_info(wt_walkback_queue)').fetchall()))
q.ensure_schema(ops);ww.deep_walkback.ensure_schema(ops);print('SCHEMA_OK')
