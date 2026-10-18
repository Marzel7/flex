"""Run focused Python tests behind the unchanged frozen startup boundary."""
import sys,runpy,pathlib,json,hashlib,os,sqlite3
root=pathlib.Path(sys.argv[1]).resolve();tests=sys.argv[2:];state=pathlib.Path(os.environ['DEV043_STATE_ROOT']);sys.dont_write_bytecode=True
sys.argv=[str(root/'docs/audits/dev043/isolation_bootstrap.py'),'--self-test']
try:runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as e:
 if e.code not in (None,0):raise
events=sqlite3.connect.__globals__['EVENTS'];expected_selftest_events=len(events)
sys.path.insert(0,str(root))
import pytest
code=pytest.main(['-q','-p','no:cacheprovider','--basetemp='+str(state/'fixtures'),*[str(root/p) for p in tests]])
# The frozen outer guard permits only compact owned fixture state. Test and
# fixture writes are still behind its filesystem/SQLite/network audit guards.
modules=[]
for name,module in list(sys.modules.items()):
 origin=getattr(module,'__file__',None)
 if origin:
  p=pathlib.Path(origin).resolve()
  if p.is_file():modules.append({'module':name,'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(state/'test-module-receipt.json').write_text(json.dumps({'source_root':str(root),'modules':modules,'isolation_events':events[expected_selftest_events:],'bindings':{'fixture_root':str(state)}},indent=2))
raise SystemExit(code)
