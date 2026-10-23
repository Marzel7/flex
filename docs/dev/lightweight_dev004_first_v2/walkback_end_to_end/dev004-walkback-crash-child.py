import os,sys
from pathlib import Path
prefix=Path('/private/tmp/dev004-walkback-complete.py').read_text().split('asyncio.run(')[0]
old="exec(compile(open('/private/tmp/dev004-walkback-run-bootstrap.py').read(),'bootstrap','exec'))"
new="exec(compile(open('/private/tmp/dev004-walkback-run-bootstrap.py').read().replace('/private/tmp/dev004-walkback-complete-run',sys.argv[1]),'bootstrap','exec'))"
exec(compile(prefix.replace(old,new),'installed-loader','exec'))
tree=ast.parse(Path('/private/tmp/dev004-walkback-complete.py').read_text());exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='tx'],type_ignores=[]),'synthetic-tx','exec'))
# Actual same birth/admission helper used by negative controls.
tree=ast.parse(Path('/private/tmp/dev004-walkback-cases.py').read_text());exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'admit','state'}],type_ignores=[]),'admission','exec'))
phase=sys.argv[2];fault=sys.argv[3];mint,creator,anchor,cls=admit('crash '+phase);account=wallet('crash account '+phase);s1=sig('crash funding1 '+phase);s2=sig('crash funding2 '+phase);count=0
transactions={s1:tx(account,creator,s1,990,2122039000,True),s2:tx(SP,account,s2,980,430000000000,True)}
def transport(method,params):
 global count;count+=1;assert count<=24
 if method=='getSignaturesForAddress':return [{'signature':s1,'slot':990,'err':None}] if params[0]==creator else [{'signature':s2,'slot':980,'err':None}] if params[0]==account else []
 if method=='getTransaction':return transactions[params[0]]
 if method=='getAccountInfo':return {'value':{'owner':ww._SYSTEM_PROGRAM}}
 raise AssertionError(method)
ww._rpc=transport;ops.close()
class CrashConnection(sqlite3.Connection):
 def commit(self):
  row=self.execute('select status from wt_walkback_queue where mint=?',(mint,)).fetchone();terminal=bool(row and row[0]=='complete' and self.execute('select 1 from wt_attribution_outcomes where mint=?',(mint,)).fetchone())
  if terminal and fault=='before':os._exit(71)
  super().commit()
  if terminal and fault=='after':os._exit(72)
ops=connect(O/'ops.sqlite',factory=CrashConnection);ops.row_factory=sqlite3.Row
if ww._mark_running(ops,mint):ww._process_row(ops,ops.execute('select * from wt_walkback_queue where mint=?',(mint,)).fetchone())
else:print('NOT_CLAIMABLE')
(O/('child_'+fault+'.json')).write_text(json.dumps({'mint':mint,'state':state(mint),'synthetic_calls':count},indent=2)+'\n');print('CHILD_FINISHED',state(mint)['queue']['status']);ops.close()
