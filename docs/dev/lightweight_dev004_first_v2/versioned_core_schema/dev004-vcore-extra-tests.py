from pathlib import Path
s=Path('/private/tmp/dev004-vcore-tests.py').read_text();exec(compile(s.split("root=fixture('fresh')")[0],'test-helpers','exec'))
for ddl,reason in [('alter table token_analysis add column first_observed_at INTEGER CHECK(first_observed_at>0)','INCOMPATIBLE_COLUMN_CONSTRAINT'),('alter table token_analysis add column create_tx_signature TEXT','INCOMPATIBLE_COLUMN_UNIQUENESS')]:
 r=fixture('constraints');c=sqlite3.connect(r/'core.sqlite');c.execute(ddl)
 if 'UNIQUE' in reason:c.execute('create unique index synthetic_incompatible_unique on token_analysis(create_tx_signature)')
 c.commit();c.close();expect_hold(r,reason)
r=fixture('failedcommit');original=m.sqlite3.connect
class FailCommit(sqlite3.Connection):
 def execute(self,sql,*a,**k):
  if sql=='COMMIT':raise sqlite3.OperationalError('SYNTHETIC_FAILED_COMMIT')
  return super().execute(sql,*a,**k)
before=schema(r)
def failing(*a,**k):k['factory']=FailCommit;return original(*a,**k)
m.sqlite3.connect=failing
try:
 try:run(r)
 except sqlite3.OperationalError:pass
 else:raise AssertionError('commit failure expected')
finally:m.sqlite3.connect=original
assert schema(r)==before and not list(r.glob('*.json'));run(r);results.append({'case':'failed_commit_rolls_back_and_retry_converges','status':'PASS'})
r=fixture('userversion');c=sqlite3.connect(r/'core.sqlite');c.execute('pragma user_version=849');c.close();run(r);c=sqlite3.connect(r/'core.sqlite');assert c.execute('pragma user_version').fetchone()[0]==849;c.close();results.append({'case':'existing_user_version_preserved','status':'PASS'})
r=fixture('badmarker');run(r);marker=next(r.glob('*.json'));value=json.loads(marker.read_text());value['contract']='invalid';marker.write_text(json.dumps(value));expect_hold(r,'INCOMPATIBLE_VERSION_MARKER')
(O/'extra.json').write_text(json.dumps({'status':'PASS','tests':results},indent=2)+'\n');print('PASS',len(results),'extra groups')
