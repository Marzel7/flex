from pathlib import Path
script=Path('/private/tmp/dev004-test-compact-history.py').read_text();exec(compile(script.split('for w in manifest[\'creators\']:')[0],'compact-test-loader','exec'))
prior=json.loads((O/'equivalence.json').read_text());NOW=prior['anchor_unix'];budgets=[]
TARGET='SELECT creator, matched_subprov, matched_treasury FROM watchtower_token_attribution WHERE creator=? LIMIT 1'
class LimitedFirstMatch(Bounded):
 def execute(self,sql,args=()):
  if sql!=TARGET:return super().execute(sql,args)
  record={'creator':args[0],'max_vm_instructions':20000,'max_seconds':.1,'instructions_at_last_callback':0};budgets.append(record);deadline=time.monotonic()+.1
  def stop():
   record['instructions_at_last_callback']+=100
   return int(record['instructions_at_last_callback']>=20000 or time.monotonic()>deadline)
  self.set_progress_handler(stop,100)
  return sqlite3.Connection.execute(self,sql,args)
src['ops'].close();src['ops']=sqlite3.connect('file:/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db?mode=ro',uri=True,factory=LimitedFirstMatch);src['ops'].row_factory=sqlite3.Row;src['ops'].execute('pragma query_only=on')
for x in prior['cases']:
 if x['classification_equal'] is not None:continue
 try:
  old=classify(legacy,src,x['creator'],False);new=classify(dev,v2,x['creator'],True);x.update(v1_class=old,v2_class=new,classification_equal=old==new,fallback_qualification='EXACT_INSTALLED_SQL_WITH_VM_AND_TIME_BOUND');x.pop('v1_class_error',None)
 except (sqlite3.Error,ValueError) as e:x['bounded_fallback_error']=str(e)
prior['classification_exact']=sum(x['classification_equal'] is True for x in prior['cases']);prior['classification_different']=sum(x['classification_equal'] is False for x in prior['cases']);prior['classification_unknown']=sum(x['classification_equal'] is None for x in prior['cases']);prior['fallback_vm_budgets']=budgets;(O/'equivalence.json').write_text(json.dumps(prior,indent=2)+'\n');print('classification exact/different/HOLD',prior['classification_exact'],prior['classification_different'],prior['classification_unknown']);print('budgeted queries',len(budgets),'max observed callback instructions',max(x['instructions_at_last_callback'] for x in budgets))
