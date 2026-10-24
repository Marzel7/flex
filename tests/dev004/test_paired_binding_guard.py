import asyncio,hashlib,importlib.util,json,os,sqlite3,sys,types
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('pair_guard_cases',ROOT/'tests/dev004/test_bounded_paired_transfer.py');p=importlib.util.module_from_spec(s);s.loader.exec_module(p)
env=p.env;no_network=p.no_network;case=p.case;NATIVE=p.NATIVE

def count(c,kind='core'):
 table='token_analysis' if kind=='core' else 'migration_walkback_handoff'
 with NATIVE(c.paths['v1']['core']) as connection:return connection.execute('SELECT count(*) FROM '+table).fetchone()[0]

def write(c,label='guard-probe',g='v1'):
 with c.conn(c.paths[g]['core']) as connection:connection.execute('INSERT INTO token_analysis(mint) VALUES(?)',(label,))

def authority(c,**updates):
 state=json.loads(c.authority.read_text());state.update(updates);c.publish_json(c.authority,state)

@pytest.mark.parametrize('core,ops',[('v2','v1'),('v1','v2')])
def test_both_mixed_directions_reject_startup_and_writes(case,core,ops):
 c=case;p.normal_prefix(c)
 with pytest.raises(c.guard.PairedBindingError):c.binding(core,ops)
 before=NATIVE(c.paths['v2']['core']).execute('SELECT count(*) FROM token_analysis').fetchone()[0]
 with pytest.raises(c.guard.PairedBindingError):write(c,g='v2')
 with NATIVE(c.paths['v2']['core']) as q:assert q.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==before


def test_valid_origin_pair_and_valid_transferred_pair(case):
 c=case;assert c.guard.validate_pair()=='fixture-v1';write(c,'origin-ok');p.normal_prefix(c);assert c.guard.validate_pair()=='fixture-v2';write(c,'target-ok','v2')

def test_retired_origin_cannot_resume(case):
 c=case;c.retire()
 with pytest.raises(c.guard.PairedBindingError):c.binding('v1')
 with pytest.raises(c.w.DatabaseRetiredError):write(c)

@pytest.mark.parametrize('fault',['missing_manifest','corrupt_manifest','missing_authority','corrupt_authority','missing_ops_seal','corrupt_core_seal','prepared','wrong_owner','incomplete','wrong_generation'])
def test_missing_ambiguous_or_partial_metadata_fails_closed(case,fault):
 c=case;before=count(c);r=c.birth.admit_birth(p.support.payload(sig='pending-guard',mint='pending-guard'))
 if fault=='missing_manifest':c.manifests['v1'].unlink()
 elif fault=='corrupt_manifest':c.manifests['v1'].write_bytes(b'{')
 elif fault=='missing_authority':c.authority.unlink()
 elif fault=='corrupt_authority':c.authority.write_bytes(b'{')
 elif fault=='missing_ops_seal':Path(str(c.paths['v1']['ops'])+'.pair-bound').unlink()
 elif fault=='corrupt_core_seal':Path(str(c.paths['v1']['core'])+'.pair-bound').write_bytes(b'{')
 elif fault=='prepared':authority(c,phase='PREPARED')
 elif fault=='wrong_owner':authority(c,owner='other')
 elif fault=='incomplete':authority(c,transfer_complete=False)
 else:authority(c,generation='not-the-manifest')
 with pytest.raises(c.guard.PairedBindingError):write(c)
 with pytest.raises(c.guard.PairedBindingError):asyncio.run(c.actor._persist_durable_birth(r))
 before_journal=c.birth.path.read_bytes()
 with pytest.raises(c.guard.PairedBindingError):c.birth.acknowledge(r['id'],'pair',lambda row:True)
 assert c.birth.path.read_bytes()==before_journal and count(c)==before


def test_replacement_connections_do_not_bypass_invalid_binding(case):
 c=case;old=c.conn(c.paths['v1']['core']);c.env.patch.setenv('WT_OPS_DB_PATH',str(c.paths['v2']['ops']));fresh=c.conn(c.paths['v1']['core'])
 for connection in [old,fresh]:
  with pytest.raises(c.guard.PairedBindingError):connection.execute("INSERT INTO token_analysis(mint) VALUES('stale-replacement')")
  connection.close()


def test_disabling_flag_cannot_bypass_bound_database(case):
 c=case;c.env.patch.setenv('DEV004_PAIRED_BINDING_REQUIRED','0');c.env.patch.delenv('FLEX_DB_PAIR_MANIFEST');c.env.patch.delenv('FLEX_DB_PAIR_AUTHORITY')
 with pytest.raises(c.guard.PairedBindingError):write(c)


def test_same_filename_wrong_physical_identity_rejected(case):
 c=case;manifest=json.loads(c.manifests['v1'].read_text());manifest['stores']['ops']['inode']+=1;c.publish_json(c.manifests['v1'],manifest)
 with pytest.raises(c.guard.PairedBindingError,match='IDENTITY_MISMATCH'):write(c)


def test_migration_retry_handoff_and_ack_are_fenced(case):
 c=case;c.migration.admit('signature-only','PUMPSWAP_MIGRATION_ACCOUNT',1);c.env.patch.setenv('WT_OPS_DB_PATH',str(c.paths['v2']['ops']))
 before=count(c,'handoff')
 with pytest.raises(c.guard.PairedBindingError):
  with c.conn(c.paths['v1']['core']) as q:c.handoff['record_intent'](q,mint='blocked',creator=None,migration_tx='signature-only',migrated_at=1,now=1)
 assert count(c,'handoff')==before
 c.migration.resolve('signature-only','blocked')
 with pytest.raises(c.guard.PairedBindingError):c.migration.acknowledge('signature-only','pair',lambda row:True)
 assert len(c.migration.pending())==1


def test_migration_dispatch_durable_admission_but_no_dispatch_when_invalid(case):
 c=case;c.env.patch.setenv('WT_OPS_DB_PATH',str(c.paths['v2']['ops']));c.scope['_signature_migration_journal']=lambda:c.migration
 p.support.listener_functions(c.scope,['_schedule_journal_migration'])
 with pytest.raises(c.guard.PairedBindingError):c.scope['_schedule_journal_migration'](types.SimpleNamespace(),'new-signature',[])
 row=next(r for r in c.migration.pending() if r['signature']=='new-signature');assert row['attempts']==0


def test_receipt_commit_ack_cannot_bypass_validation(case):
 c=case;mod=p.support.load('paired_guard_receipt',ROOT/'runtime/listener/src/core/pumpportal_birth_audit.py');audit=mod.PumpPortalBirthAudit(enabled=False,path=str(c.env.path/'raw-unused'),compact_path=str(c.env.path/'receipts'))
 identity=audit.record(receive_utc_ns=1,receive_monotonic_ns=1,parser_utc_ns=1,mint='1'*32,creator='1'*32,signature='1'*64)
 files=list((c.env.path/'receipts').rglob('*.json'));assert len(files)==1;before=files[0].read_bytes();c.env.patch.setenv('WT_OPS_DB_PATH',str(c.paths['v2']['ops']))
 with pytest.raises(c.guard.PairedBindingError):audit.record_outcome(identity,outcome='COMMITTED',canonical=('1'*32,'1'*32,'1'*64))
 assert files[0].read_bytes()==before


def test_partial_seal_cannot_activate_and_can_resume_without_loss(case,monkeypatch):
 c=case;c.admit_during_lease();c.retire();c.reconcile();original=os.open;ops_seal=str(c.paths['v2']['ops'])+'.pair-bound'
 def fail(path,*a,**kw):
  if str(path)==ops_seal:raise OSError('simulated crash between identity seals')
  return original(path,*a,**kw)
 monkeypatch.setattr(os,'open',fail)
 with pytest.raises(OSError):c.guard.seal_manifest(str(c.manifests['v2']))
 monkeypatch.setattr(os,'open',original)
 c.select_authority('v2','ACTIVE');c.bind_environment('v2')
 with pytest.raises(c.guard.PairedBindingError):c.binding('v2')
 assert c.birth.pending() and c.migration.pending()
 c.guard.seal_manifest(str(c.manifests['v2']));c.binding('v2');c.drain_births();c.finish_migration();assert not c.birth.pending() and not c.migration.pending()


def test_transfer_owner_and_ack_mode_enforced(case):
 c=case;c.admit_during_lease();c.retire();c.prepare_manifest('v2');c.select_authority('v2','TRANSFER');c.bind_environment('v2',True)
 c.env.patch.setenv('FLEX_DB_PAIR_OWNER','other')
 with pytest.raises(c.guard.PairedBindingError):write(c,g='v2')
 c.env.patch.setenv('FLEX_DB_PAIR_OWNER','fixture-transfer-owner');write(c,'transfer-row','v2')
 with pytest.raises(c.guard.PairedBindingError):c.birth.acknowledge(c.birth.pending()[0]['id'],'pair',lambda row:True)
 with pytest.raises(c.guard.PairedBindingError):c.binding('v2')


def test_new_generation_requires_retired_predecessors(case):
 c=case;c.prepare_manifest('v2');c.guard.seal_manifest(str(c.manifests['v2']));c.select_authority('v2','ACTIVE');c.bind_environment('v2')
 with pytest.raises(c.guard.PairedBindingError):c.binding('v2')
 with pytest.raises(c.guard.PairedBindingError):write(c,g='v2')


def test_abort_before_retirement_rolls_back_to_authorized_origin(case):
 c=case;c.prepare_manifest('v2');c.guard.seal_manifest(str(c.manifests['v2']));c.select_authority('v2','ACTIVE');c.bind_environment('v2')
 with pytest.raises(c.guard.PairedBindingError):c.binding('v2')
 c.select_authority('v1','ACTIVE');c.bind_environment('v1');c.binding('v1');write(c,'rollback-before-retirement')
 assert c.guard.validate_pair()=='fixture-v1'


def test_commit_rechecks_authority_and_rolls_back_staged_row(case):
 c=case;q=c.conn(c.paths['v1']['core']);q.execute("INSERT INTO token_analysis(mint) VALUES('uncommitted')");authority(c,phase='PREPARED')
 with pytest.raises(c.guard.PairedBindingError):q.commit()
 q.close()
 with NATIVE(c.paths['v1']['core']) as r:assert r.execute("SELECT 1 FROM token_analysis WHERE mint='uncommitted'").fetchone() is None


def test_authorized_matched_rollback_copy_not_retired_origin(case):
 c=case;p.normal_prefix(c);c.drain_births();c.finish_migration()
 # Provisioning fresh non-authoritative fixture stores is not an unretire.
 rollback={k:c.env.path/('rollback-'+k+'.sqlite') for k in ['core','ops']}
 for kind,path in rollback.items():
  with NATIVE(path) as connection:
   for ddl in p.SCHEMA[kind]:connection.execute(ddl)
 current=json.loads(c.manifests['v2'].read_text());manifest={'version':1,'authority':current['authority'],'generation':'fixture-rollback','stores':{k:c.guard.database_identity(str(rollback[k]),'fixture-rollback',k) for k in ['core','ops']},'predecessor':{'generation':current['generation'],'stores':current['stores']}}
 path=c.env.path/'rollback-runtime-manifest.json';c.publish_json(path,manifest)
 c.w.retire_database('core',c.paths['v2']['core'],generation='fixture-v2',timeout=.2);c.w.retire_database('ops',c.paths['v2']['ops'],generation='fixture-v2',timeout=.2)
 state=json.loads(c.authority.read_text());state.update({'generation':'fixture-rollback','manifest_path':str(path),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'phase':'TRANSFER','owner':'fixture-transfer-owner','transfer_complete':False});c.publish_json(c.authority,state)
 for name,value in {'DB_PATH':str(rollback['core']),'WT_OPS_DB_PATH':str(rollback['ops']),'FLEX_DB_PAIR_MANIFEST':str(path),'FLEX_DB_PAIR_ROLE':'transfer','FLEX_DB_PAIR_OWNER':'fixture-transfer-owner'}.items():c.env.patch.setenv(name,value)
 for kind,tables in [('core',p.CORE_TABLES),('ops',p.OPS_TABLES)]:
  for table in tables:
   rows=c.rows(c.paths['v2'][kind],table)
   with c.conn(rollback[kind]) as target:
    for row in rows:
     columns=list(row);target.execute('INSERT INTO "'+table+'"('+','.join('"'+x+'"' for x in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row[x] for x in columns))
 c.guard.seal_manifest(str(path));state.update({'phase':'ACTIVE','owner':'runtime','transfer_complete':True});c.publish_json(c.authority,state);c.env.patch.setenv('FLEX_DB_PAIR_ROLE','runtime')
 db=p.support.load('rollback_actual_db',ROOT/'runtime/listener/src/core/db.py');assert db.DB_PATH==str(rollback['core']);assert db.resolve_ops_db_path()==str(rollback['ops']);assert c.guard.validate_pair()=='fixture-rollback'
 for kind,tables in [('core',p.CORE_TABLES),('ops',p.OPS_TABLES)]:
  for table in tables:assert c.rows(rollback[kind],table)==c.rows(c.paths['v2'][kind],table)
 with c.conn(rollback['core']) as target:target.execute("INSERT INTO token_analysis(mint) VALUES('authorized-rollback-write')")
 assert Path(str(c.paths['v1']['core'])+'.retired').exists() and Path(str(c.paths['v2']['core'])+'.retired').exists()

def test_competing_authority_path_is_not_another_valid_owner(case):
 c=case;other=c.env.path/'competing-authority.json';other.write_bytes(c.authority.read_bytes());c.env.patch.setenv('FLEX_DB_PAIR_AUTHORITY',str(other))
 with pytest.raises(c.guard.PairedBindingError,match='AUTHORITY_PATH'):write(c)


def test_wrong_store_generation_or_role_cannot_activate(case):
 c=case;manifest=json.loads(c.manifests['v1'].read_text());manifest['stores']['ops']['generation']='other-generation';c.publish_json(c.manifests['v1'],manifest)
 with pytest.raises(c.guard.PairedBindingError,match='GENERATION_IDENTITY'):write(c)

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
def test_all_runtime_startup_modules_validate_pair(case,component):
 c=case;db=p.support.load('guard_startup_'+component,ROOT/f'runtime/{component}/src/core/db.py');assert db.DB_PATH==str(c.paths['v1']['core'])
 c.env.patch.setenv('WT_OPS_DB_PATH',str(c.paths['v2']['ops']))
 with pytest.raises(c.guard.PairedBindingError):p.support.load('guard_startup_invalid_'+component,ROOT/f'runtime/{component}/src/core/db.py')


def test_seal_retry_resyncs_existing_marker_after_publication_failure(case,monkeypatch):
 import stat
 c=case;c.admit_during_lease();c.retire();c.reconcile();original=os.fsync;failed=[False]
 def fail(fd):
  if stat.S_ISREG(os.fstat(fd).st_mode) and not failed[0]:failed[0]=True;raise OSError('simulated seal file fsync')
  return original(fd)
 monkeypatch.setattr(os,'fsync',fail)
 with pytest.raises(OSError):c.guard.seal_manifest(str(c.manifests['v2']))
 monkeypatch.setattr(os,'fsync',original);calls=[]
 def record(fd):calls.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file');return original(fd)
 monkeypatch.setattr(os,'fsync',record);c.guard.seal_manifest(str(c.manifests['v2']));assert calls.count('file')==2 and calls.count('directory')==2
 c.select_authority('v2','ACTIVE');c.bind_environment('v2');c.binding('v2');assert c.guard.validate_pair()=='fixture-v2'


def test_retirement_retry_resyncs_readable_marker(case,monkeypatch):
 import stat
 c=case;c.w.retire_database('core',c.paths['v1']['core'],generation='fixture-v1',timeout=.2);calls=[];original=os.fsync
 def record(fd):calls.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file');return original(fd)
 monkeypatch.setattr(os,'fsync',record);c.w.retire_database('core',c.paths['v1']['core'],generation='fixture-v1',timeout=.2);assert calls==['file','directory']


def test_pair_metadata_file_size_limit(case):
 c=case;c.authority.write_bytes(b'x'*8193)
 with pytest.raises(c.guard.PairedBindingError,match='CAPACITY'):write(c)


def test_authority_change_inside_ack_verification_cannot_ack(case):
 c=case;r=c.birth.admit_birth(p.support.payload(sig='deferred',mint='deferred'));before=c.birth.path.read_bytes()
 def verify(row):authority(c,phase='PREPARED');return True
 with pytest.raises(c.guard.PairedBindingError):c.birth.acknowledge(r['id'],'pair',verify)
 assert c.birth.path.read_bytes()==before


def test_authority_change_during_receipt_read_cannot_publish(case,monkeypatch):
 c=case;mod=p.support.load('paired_guard_receipt_recheck',ROOT/'runtime/listener/src/core/pumpportal_birth_audit.py');audit=mod.PumpPortalBirthAudit(enabled=False,path=str(c.env.path/'unused'),compact_path=str(c.env.path/'receipts-recheck'))
 identity=audit.record(receive_utc_ns=1,receive_monotonic_ns=1,parser_utc_ns=1,mint='1'*32,creator='1'*32,signature='1'*64);file=next((c.env.path/'receipts-recheck').rglob('*.json'));before=file.read_bytes();read=audit._read_receipt
 def read_then_change(path):r=read(path);authority(c,phase='PREPARED');return r
 monkeypatch.setattr(audit,'_read_receipt',read_then_change)
 with pytest.raises(c.guard.PairedBindingError):audit.record_outcome(identity,outcome='COMMITTED',canonical=('1'*32,'1'*32,'1'*64))
 assert file.read_bytes()==before
