"""Current listener guard, exact production preimage, no DB/network acquisition."""
import ast,asyncio,hashlib,os,types
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
LISTENER=ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py'
GUARD='''        # The paired V2 lifecycle owns age-based disposal. Legacy maintenance
        # prunes accounting and sub-seven-day evidence outside that contract.
        if os.environ.get("DEV004_PAIRED_BINDING_REQUIRED") == "1":
            return

'''

def test_only_accepted_guard_against_installed_preimage():
    s=LISTENER.read_text();assert s.count(GUARD)==1
    assert hashlib.sha256(s.replace(GUARD,'',1).encode()).hexdigest()=='15e31d294371d18302c04209775c130237a9ba220080c17aec3bda281856750c'

@pytest.mark.parametrize('binding',[None,'/FORBIDDEN_RETIRED_V1_REFERENCE_ONLY.sqlite','/unqualified.sqlite'])
def test_paired_guard_before_database_access(monkeypatch,binding):
    source=LISTENER.read_text();node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_db_maintenance_periodic')
    calls=[]
    def forbidden(*a,**k):calls.append(True);raise AssertionError('DATABASE_ACCESS_FORBIDDEN')
    ns={'os':os,'DB_PATH':binding,'db_connect':forbidden};exec(compile(ast.Module(body=[node],type_ignores=[]),'current-maintenance-guard','exec'),ns);monkeypatch.setenv('DEV004_PAIRED_BINDING_REQUIRED','1');asyncio.run(ns['_db_maintenance_periodic'](object()));assert calls==[]

def test_maintenance_disabled_startup_branch_unchanged(monkeypatch):
    tree=ast.parse(LISTENER.read_text());node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and 'LISTENER_DB_MAINTENANCE_ENABLED' in ast.unparse(n.test))
    def forbidden(*a,**k):raise AssertionError('DISABLED_MAINTENANCE_SCHEDULED')
    ns={'asyncio':types.SimpleNamespace(create_task=forbidden),'self':types.SimpleNamespace(_db_maintenance_periodic=forbidden),'log_print':lambda *a,**k:None};monkeypatch.setenv('LISTENER_DB_MAINTENANCE_ENABLED','0');exec(compile(ast.Module(body=[node],type_ignores=[]),'unchanged-disabled-startup','exec'),ns)

def test_recovery_claim_correction_unchanged():
    p=ROOT/'scripts/flex_v2_fresh_pair_recovery.py'
    assert hashlib.sha256(p.read_bytes()).hexdigest()=='50967f4cd1109dd8cdae78acb629fa586b7ad2733e55b0bc5dd5e257871edcc5'

def test_only_required_renderer_pins_changed():
    path=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py';s=path.read_text();new=hashlib.sha256(LISTENER.read_bytes()).hexdigest();assert s.count(new)==2
    assert hashlib.sha256(s.replace(new,'15e31d294371d18302c04209775c130237a9ba220080c17aec3bda281856750c').encode()).hexdigest()=='203438f7d356d65df1424080ce85755c46d83b3ca70e9ec255dd772be3691811'
