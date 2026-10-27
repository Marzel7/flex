import hashlib,json,os
import pytest
from tests.test_v2_helius_recovery_config import case,base_case,target,activate

def prepare(e,monkeypatch):
 cls=__import__('importlib.util').util.spec_from_file_location
 spec=cls('pending_recovery',e.package/'src/core/migration_terminal_evidence.py');m=__import__('importlib.util').util.module_from_spec(spec);spec.loader.exec_module(m)
 e.pending=m.DeferredMigrationStore(e.ops,e.journal.path,generation='source',validate_record=e.journal._validate,validate_pair=lambda p:e.g.validate_pair(core_path=e.env['DB_PATH'],ops_path=str(p)))
 e.pending.initialize();row=e.journal._new('deferred','PUMPSWAP',100,None,None);e.pending.preserve_batch([row]);e.pending_row=row
 e.env['DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED']='1';e.plan['planned_production_environment'].update(e.env);monkeypatch.setenv('DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED','1')
 return m

def test_deferred_fresh_pair_preserves_custody_and_generation(case,monkeypatch):
 e=case;m=prepare(e,monkeypatch);e.ns['write_config']('v2');paths,manifest,dest=target(e);e.ns['freeze_helius_recovery'](e.ns['prepare_helius_recovery'](e.env,dest));env=activate(e,paths,manifest,dest,monkeypatch);e.ns['finish_helius_recovery'](env)
 pending=m.DeferredMigrationStore(paths['ops'],e.journal.path,generation='fresh',validate_record=e.journal._validate,validate_pair=lambda p:e.g.validate_pair(core_path=env['DB_PATH'],ops_path=str(p)))
 assert pending.lookup('deferred',owned_only=True)==e.pending_row
 e.running[0]=False
 e.ns['settings']=lambda _:dict(env);e.ns['write_config']('v2')
 assert e.output_env()['DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED']=='1'
 import configparser
 from supervisor.datatypes import dict_of_key_value_pairs
 cfg=configparser.ConfigParser(interpolation=None);cfg.read(e.live)
 for service in ['creator_resolution_worker','walkback_worker']:
  assert 'DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED' not in dict_of_key_value_pairs(cfg['program:'+service]['environment'])

@pytest.mark.parametrize('bad',['missing_flag','flag0','missing_schema','wrong_generation'])
def test_deferred_recovery_rejects_ambiguous_configuration(case,monkeypatch,bad):
 e=case;prepare(e,monkeypatch);before=e.live.read_bytes()
 if bad=='missing_flag':e.env.pop('DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED')
 elif bad=='flag0':e.plan['planned_production_environment']['DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED']='0';e.env['DEV004_MIGRATION_DEFERRED_EVIDENCE_ENABLED']='0'
 elif bad=='missing_schema':
  import sqlite3
  with sqlite3.connect(e.ops) as c:c.execute('drop table migration_deferred_evidence_v1')
 else:e.env['WT_OPS_DB_PATH']=str(e.root/'wrong.sqlite')
 with pytest.raises(Exception):e.ns['terminal_environment'](e.env)
 assert e.live.read_bytes()==before
