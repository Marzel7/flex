import sqlite3
from src.ops.watchtower_price_lifecycle_jobs import enqueue_after_assignment_commit,advance_from_retained_facts,reconcile_committed_assignments

def assignment(): return {"operation_id":"watchtower","mint":"mint","event_id":"assignment-1","assigned_at":10}

def test_post_commit_idempotent_job_and_disabled_flag_boundary():
 c=sqlite3.connect(":memory:")
 assert enqueue_after_assignment_commit(c,assignment(),assignment_committed=False)["status"]=="NOT_ENQUEUED"
 assert enqueue_after_assignment_commit(c,assignment(),assignment_committed=True,now=1)["status"]=="ENQUEUED"
 assert enqueue_after_assignment_commit(c,assignment(),assignment_committed=True,now=2)["status"]=="ALREADY_ENQUEUED"

def test_retained_only_stages_never_require_transport():
 c=sqlite3.connect(":memory:"); job=enqueue_after_assignment_commit(c,assignment(),assignment_committed=True)["assignment_identity"]
 assert advance_from_retained_facts(c,job,{})["status"]=="WAITING_FOR_OPENING"
 opening={"qualified":True,"market_cap":100,"timestamp":11,"method":"FIRST_FULL_POST_MIGRATION_SECOND_MC"}
 assert advance_from_retained_facts(c,job,{"opening":opening})["status"]=="WAITING_FOR_OBSERVED_PEAK"
 peak={"market_cap":200,"timestamp":20,"observed":True}
 assert advance_from_retained_facts(c,job,{"opening":opening,"observed_peak":peak})["status"]=="WAITING_FOR_PREPEAK_OBSERVATION"
 sparse={"qualification_status":"OBSERVED_SPARSE","maximum_observed_prepeak_drawdown_percent":50}
 assert advance_from_retained_facts(c,job,{"opening":opening,"observed_peak":peak,"prepeak":sparse,"postpeak":{"observed":True}})["status"]=="COMPLETE"

def test_disabled_reconcile_creates_nothing(monkeypatch,tmp_path):
 db=tmp_path/"ops.db"; c=sqlite3.connect(db); c.execute("CREATE TABLE operator_launch_membership(mint TEXT,event_id TEXT,assigned_at INTEGER,operator_id TEXT)"); c.execute("INSERT INTO operator_launch_membership VALUES('mint','event',1,'watchtower')"); c.commit(); c.close()
 monkeypatch.delenv("WATCHTOWER_PRICE_LIFECYCLE_ENABLED",raising=False)
 assert reconcile_committed_assignments(str(db))==0
 c=sqlite3.connect(db); assert c.execute("SELECT name FROM sqlite_master WHERE name='watchtower_price_lifecycle_jobs'").fetchone() is None

def test_enabled_reconcile_projects_committed_membership_once(monkeypatch,tmp_path):
 db=tmp_path/"ops.db"; c=sqlite3.connect(db); c.execute("CREATE TABLE operator_launch_membership(mint TEXT,event_id TEXT,assigned_at INTEGER,operator_id TEXT)"); c.execute("INSERT INTO operator_launch_membership VALUES('mint','event',1,'watchtower')"); c.commit(); c.close()
 monkeypatch.setenv("WATCHTOWER_PRICE_LIFECYCLE_ENABLED","true")
 assert reconcile_committed_assignments(str(db),now=2)==1
 assert reconcile_committed_assignments(str(db),now=3)==0
 c=sqlite3.connect(db); assert c.execute("SELECT mint,state FROM watchtower_price_lifecycle_jobs").fetchone()==("mint","WAITING_FOR_OPENING")
