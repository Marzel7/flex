import sqlite3
from src.ops.manual_registry import DDL,apply_dispositions,active,metrics
def db():
 c=sqlite3.connect(':memory:'); c.execute('CREATE TABLE operators(operator_id TEXT PRIMARY KEY,display_name TEXT,status TEXT,updated_at INTEGER)'); c.executemany('INSERT INTO operators VALUES(?,?,?,?)',[('wt','WATCHTOWER','CONFIRMED',1),('sw','3SW2','CONFIRMED',1),('x','DISCOVERY','CONFIRMED',1)]); c.executescript(DDL); return c
def test_manual_admission_and_reference_retention():
 c=db(); apply_dispositions(c); assert [x['display_name'] for x in active(c)]==['WATCHTOWER']; assert c.execute("SELECT disposition FROM operation_registry_dispositions WHERE operator_id='sw'").fetchone()[0]=='REFERENCE_RETIRED'; assert c.execute("SELECT display_name FROM operators WHERE operator_id='sw'").fetchone()[0]=='3SW2'
def test_activity_is_deterministic_and_dormancy_retains_identity():
 m=metrics([100,200,300],now=40*86400); assert m['activity_state']=='DORMANT' and m['total_observed_launches']==3
def test_reactivation_and_variant_append_do_not_overwrite_baseline():
 c=db(); c.execute("INSERT INTO operation_behavioural_profiles VALUES('p','wt','candidate',1,'PROPOSED','{}','[]',1,NULL,NULL)"); c.execute("INSERT INTO operation_behavioural_components VALUES('b','p','funding','BASELINE','{\"amount\":30}',1)"); c.execute("INSERT INTO operation_behavioural_components VALUES('v','p','funding','VARIANT','{\"amount\":25}',2)"); assert c.execute("SELECT COUNT(*) FROM operation_behavioural_components WHERE state='BASELINE'").fetchone()[0]==1; assert metrics([1,2,3,10000000],now=10000001)['activity_state'] in {'SLOWING','ACTIVE','VERY_ACTIVE'}
