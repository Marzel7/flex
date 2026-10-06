import sqlite3
import pytest
from src.ops.retained_injector_opening import MINT,EVIDENCE,import_retained_injector_opening

def db(path):
 c=sqlite3.connect(path);c.execute("create table operation_monitor_facts(operation_id text,mint text,entry_status text,entry_timestamp integer,entry_mc_usd real,entry_method text,entry_exactness text,monitor_state text,next_observation_at integer,evidence_status text,provenance_digest text,updated_at integer,primary key(operation_id,mint))");c.execute("insert into operation_monitor_facts values('watchtower',?,'WAITING_FOR_ENTRY_REFERENCE',null,null,'FIRST_FULL_POST_MIGRATION_SECOND_MC','UNQUALIFIED','WAITING_FOR_ENTRY_REFERENCE',null,null,'x',0)",(MINT,));c.commit();c.close()
def test_import_is_singleton_historical_and_idempotent_conflict(tmp_path):
 p=tmp_path/'x.db';db(p);r=import_retained_injector_opening(str(p),now=1);assert r['route']=='HISTORICAL_RECONSTRUCTION'
 c=sqlite3.connect(p);x=c.execute("select entry_timestamp,entry_mc_usd,entry_method,entry_exactness,entry_offset_seconds,monitor_state from operation_monitor_facts").fetchone();assert x==(1791307930,134293.7821862771,'BOUNDED_POST_MIGRATION_MC_FALLBACK','POST_MIGRATION_OFFSET_2S_OBSERVED_MC',2,'HISTORICAL_RECOVERY_ACQUIRING')
 assert c.execute("select state from watchtower_historical_openings where mint=?",(MINT,)).fetchone()==('QUALIFIED',)
 with pytest.raises(ValueError): import_retained_injector_opening(str(p),now=2)
def test_import_rejects_any_modified_evidence(tmp_path):
 p=tmp_path/'x.db';db(p);bad=dict(EVIDENCE);bad['selected_mc']=1
 with pytest.raises(ValueError): import_retained_injector_opening(str(p),bad)
