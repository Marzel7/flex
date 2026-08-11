import hashlib
import time
from pathlib import Path
from src.core.pumpportal_migration_census import PumpPortalMigrationCensus

LISTENER_SOURCE = Path(__file__).resolve().parents[1] / "src/core/pumpfun_curve_listener.py"

def wait(census):
    deadline=time.time()+1
    while census.health()["pending"] and time.time()<deadline: time.sleep(.01)

def test_default_off_does_not_write(tmp_path):
    c=PumpPortalMigrationCensus(enabled=False,path=str(tmp_path/'c.jsonl'))
    c.record(receive_utc_ns=1,receive_monotonic_ns=2,signature='sig',mint='mint')
    assert c.events()==[] and c.health()['valid_total']==0

def test_valid_duplicate_identity_and_readback(tmp_path):
    c=PumpPortalMigrationCensus(enabled=True,path=str(tmp_path/'c.jsonl'),max_pending=2)
    c.record(receive_utc_ns=1,receive_monotonic_ns=2,signature='sig',mint='mint')
    c.record(receive_utc_ns=3,receive_monotonic_ns=4,signature='sig',mint='mint')
    c.record(receive_utc_ns=5,receive_monotonic_ns=6,signature='sig2',mint='mint')
    wait(c); rows=c.events()
    assert len(rows)==2 and c.health()['duplicate_total']==1
    assert rows[0]['event_id']==hashlib.sha256(b'sig:mint').hexdigest()
    assert len({x['event_id'] for x in rows})==2

def test_invalid_events_are_counted_without_writes(tmp_path):
    c=PumpPortalMigrationCensus(enabled=True,path=str(tmp_path/'c.jsonl'))
    c.record(receive_utc_ns=1,receive_monotonic_ns=1,signature=None,mint='m')
    c.record(receive_utc_ns=1,receive_monotonic_ns=1,signature='s',mint=None)
    c.record(receive_utc_ns=1,receive_monotonic_ns=1,signature='',mint='')
    h=c.health()
    assert c.events()==[] and h['migration_census_invalid_total']==3
    assert h['migration_census_invalid_reasons']=={'MISSING_SIGNATURE':1,'MISSING_MINT':1,'MISSING_SIGNATURE_AND_MINT':1}
    assert h['last_invalid_reason']=='MISSING_SIGNATURE_AND_MINT'

def test_overflow_is_explicit_and_fail_open(tmp_path):
    c=PumpPortalMigrationCensus(enabled=True,path=str(tmp_path/'c.jsonl'),max_pending=1)
    c._started=True # prevent the isolated writer from draining before overflow
    c.record(receive_utc_ns=1,receive_monotonic_ns=1,signature='a',mint='m')
    c.record(receive_utc_ns=1,receive_monotonic_ns=1,signature='b',mint='m')
    assert c.health()['dropped']==1

def test_listener_routes_observed_migrate_and_legacy_migration_spellings():
    source = LISTENER_SOURCE.read_text()
    assert 'elif tx_type in ("migration", "migrate"):' in source

def test_listener_census_runs_before_business_signature_guard():
    source = LISTENER_SOURCE.read_text()
    branch = source.split('elif tx_type in ("migration", "migrate"):', 1)[1]
    branch = branch.split('\n            except Exception as e:', 1)[0]
    assert branch.index("self._migration_census.record") < branch.index("if sig and sig not in self.completed_migrations")
