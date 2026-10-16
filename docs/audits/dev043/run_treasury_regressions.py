"""Run compact existing treasury regressions with no provider or canonical access."""
import os,sys,pathlib,sqlite3,resource
ROOT=pathlib.Path(__file__).resolve().parents[3];STATE=pathlib.Path('/private/tmp/dev043-treasury-tests');STATE.mkdir(exist_ok=True);os.chdir(STATE)
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';sys.path.insert(0,str(ROOT));resource.setrlimit(resource.RLIMIT_FSIZE,(8388608,8388608))
def audit(event,args):
 if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen'):raise PermissionError(event)
 if event=='open' and not isinstance(args[0],int):
  mode=args[1] or '';flags=args[2]
  if any(x in mode for x in 'wax+') or flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
   if not pathlib.Path(args[0]).resolve().is_relative_to(STATE):raise PermissionError(str(args[0]))
sys.addaudithook(audit)
original=sqlite3.connect
def connect(db,*a,**kw):
 assert db==':memory:' or pathlib.Path(db).resolve().is_relative_to(STATE)
 return original(db,*a,**kw)
sqlite3.connect=connect
import pytest
sys.exit(pytest.main(['-q','-p','no:cacheprovider','--log-file='+str(STATE/'pytest.log'),'--basetemp='+str(STATE/'pytest'),*[str(ROOT/'tests'/f) for f in ['test_x78_2_treasury_review_matching_workspace.py','test_x78_16_treasury_review_comparison.py','test_x78_17_treasury_review_ux.py']]]))
