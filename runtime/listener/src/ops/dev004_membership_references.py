"""DEV004 fresh-V2 typed membership references; no synthetic identity events.

Original five membership columns/IDs remain intact. Atomic trigger projection
selects the real parent namespace; a typed sidecar enforces real composite FKs.
Only explicit disposable initialization is authorized by this module.
"""
import os,resource,sqlite3,hashlib,json
from pathlib import Path
MAX_FILE=64*1024*1024
MAX_MEMBERSHIPS=50_000
MEMBERSHIP='''CREATE TABLE operator_launch_membership (
 mint TEXT PRIMARY KEY,
 operator_id TEXT NOT NULL REFERENCES operators(operator_id),
 source_population_id TEXT,
 assigned_at INTEGER NOT NULL,
 event_id TEXT
)'''
REFERENCE='''CREATE TABLE operator_membership_reference_v1 (
 mint TEXT PRIMARY KEY REFERENCES operator_launch_membership(mint) ON DELETE CASCADE,
 operator_id TEXT NOT NULL REFERENCES operators(operator_id),
 legacy_event_id TEXT,
 reference_kind TEXT NOT NULL CHECK(reference_kind IN ('NONE','IDENTITY_EVENT','CONFIRMED_MATCH')),
 identity_event_id TEXT,
 confirmed_match_id TEXT,
 CHECK(length(CAST(mint AS BLOB)) BETWEEN 1 AND 128),
 CHECK(length(CAST(operator_id AS BLOB)) BETWEEN 1 AND 128),
 CHECK(legacy_event_id IS NULL OR length(CAST(legacy_event_id AS BLOB)) BETWEEN 1 AND 128),
 CHECK((reference_kind='NONE' AND legacy_event_id IS NULL AND identity_event_id IS NULL AND confirmed_match_id IS NULL)
 OR (reference_kind='IDENTITY_EVENT' AND legacy_event_id IS NOT NULL AND identity_event_id=legacy_event_id AND confirmed_match_id IS NULL)
 OR (reference_kind='CONFIRMED_MATCH' AND legacy_event_id IS NOT NULL AND confirmed_match_id=legacy_event_id AND identity_event_id IS NULL)),
 FOREIGN KEY(identity_event_id,operator_id) REFERENCES operator_identity_events(event_id,operator_id),
 FOREIGN KEY(confirmed_match_id,operator_id,mint) REFERENCES confirmed_operation_matches(match_id,operator_id,mint)
)'''
# Explicit IS NOT NULL is also required below: SQL CHECK permits NULL results.
REFERENCE=REFERENCE.replace("identity_event_id=legacy_event_id AND", "identity_event_id IS NOT NULL AND identity_event_id=legacy_event_id AND").replace("confirmed_match_id=legacy_event_id AND", "confirmed_match_id IS NOT NULL AND confirmed_match_id=legacy_event_id AND")
IDENTITY="EXISTS(SELECT 1 FROM operator_identity_events e WHERE e.event_id=NEW.event_id AND e.operator_id=NEW.operator_id)"
MATCH="EXISTS(SELECT 1 FROM confirmed_operation_matches m WHERE m.match_id=NEW.event_id AND m.operator_id=NEW.operator_id AND m.mint=NEW.mint AND m.state='CONFIRMED_MATCH')"
KIND=f"CASE WHEN NEW.event_id IS NULL THEN 'NONE' WHEN {IDENTITY} THEN 'IDENTITY_EVENT' ELSE 'CONFIRMED_MATCH' END"
ID=f"CASE WHEN NEW.event_id IS NOT NULL AND {IDENTITY} THEN NEW.event_id ELSE NULL END"
MATCHID=f"CASE WHEN NEW.event_id IS NOT NULL AND {MATCH} THEN NEW.event_id ELSE NULL END"
OBJECTS={
 'dev004_identity_event_operator_key':"CREATE UNIQUE INDEX dev004_identity_event_operator_key ON operator_identity_events(event_id,operator_id)",
 'dev004_confirmed_match_operator_mint_key':"CREATE UNIQUE INDEX dev004_confirmed_match_operator_mint_key ON confirmed_operation_matches(match_id,operator_id,mint)",
 'operator_membership_reference_v1':REFERENCE,
 'ix_operator_launch_membership_operator':"CREATE INDEX ix_operator_launch_membership_operator ON operator_launch_membership(operator_id)",
}
for action in ['INSERT','UPDATE']:
 name='dev004_membership_validate_'+action.lower()
 cap="SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM operator_launch_membership WHERE mint=NEW.mint) AND EXISTS(SELECT 1 FROM operator_launch_membership LIMIT 1 OFFSET 49999) THEN RAISE(ABORT,'membership capacity') END;" if action=='INSERT' else "SELECT CASE WHEN NEW.mint IS NOT OLD.mint THEN RAISE(ABORT,'membership identity is immutable') END;"
 OBJECTS[name]=f"""CREATE TRIGGER {name} BEFORE {action} ON operator_launch_membership BEGIN
 {cap}
 SELECT CASE WHEN NEW.event_id IS NOT NULL AND (({IDENTITY})+({MATCH}))<>1 THEN RAISE(ABORT,'unknown or ambiguous membership reference') END;
 END"""
OBJECTS['dev004_membership_project_insert']=f"""CREATE TRIGGER dev004_membership_project_insert AFTER INSERT ON operator_launch_membership BEGIN
 INSERT INTO operator_membership_reference_v1(mint,operator_id,legacy_event_id,reference_kind,identity_event_id,confirmed_match_id)
 VALUES(NEW.mint,NEW.operator_id,NEW.event_id,{KIND},{ID},{MATCHID}); END"""
OBJECTS['dev004_membership_project_update']=f"""CREATE TRIGGER dev004_membership_project_update AFTER UPDATE ON operator_launch_membership BEGIN
 UPDATE operator_membership_reference_v1 SET operator_id=NEW.operator_id,legacy_event_id=NEW.event_id,reference_kind={KIND},identity_event_id={ID},confirmed_match_id={MATCHID} WHERE mint=NEW.mint;
 SELECT CASE WHEN changes()<>1 THEN RAISE(ABORT,'missing typed membership reference') END; END"""
for action in ['INSERT','UPDATE']:
 name='dev004_reference_validate_'+action.lower()
 immutable="""SELECT CASE WHEN EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=OLD.mint AND m.operator_id=OLD.operator_id AND m.event_id IS OLD.legacy_event_id) AND (NEW.mint IS NOT OLD.mint OR NEW.operator_id IS NOT OLD.operator_id OR NEW.legacy_event_id IS NOT OLD.legacy_event_id OR NEW.reference_kind IS NOT OLD.reference_kind OR NEW.identity_event_id IS NOT OLD.identity_event_id OR NEW.confirmed_match_id IS NOT OLD.confirmed_match_id) THEN RAISE(ABORT,'typed reference is canonical projection') END;""" if action=='UPDATE' else ''
 OBJECTS[name]=f"""CREATE TRIGGER {name} BEFORE {action} ON operator_membership_reference_v1 BEGIN
 {immutable}
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=NEW.mint AND m.operator_id=NEW.operator_id AND m.event_id IS NEW.legacy_event_id) THEN RAISE(ABORT,'typed reference disagrees with membership') END; END"""
OBJECTS['dev004_reference_preserve']= """CREATE TRIGGER dev004_reference_preserve BEFORE DELETE ON operator_membership_reference_v1 WHEN EXISTS(SELECT 1 FROM operator_launch_membership WHERE mint=OLD.mint) BEGIN SELECT RAISE(ABORT,'typed reference is required'); END"""
class ReferenceContractError(RuntimeError):pass
def _production_profile(path, root, role, schema_authority):
    """Explicit frozen profile; never a fallback for an invalid DEV path."""
    import stat
    def fail(code):
        raise RuntimeError('PRODUCTION_BOOTSTRAP_' + code)
    def physical(value):
        if not isinstance(value, str) or not os.path.isabs(value) or str(Path(value).resolve()) != value or '..' in Path(value).parts:
            fail('PHYSICAL_PATH_REQUIRED')
        return Path(value)
    def read(value, cap):
        p = physical(value)
        info = p.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > cap:
            fail('METADATA_IDENTITY_OR_CAPACITY')
        raw = p.read_bytes()
        if len(raw) > cap or p.stat().st_ino != info.st_ino:
            fail('METADATA_CHANGED')
        return json.loads(raw), raw
    frozen_path = os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST')
    frozen_hash = os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_SHA256')
    if not frozen_path or not frozen_hash:
        fail('FROZEN_MANIFEST_REQUIRED')
    frozen, frozen_raw = read(frozen_path, 65536)
    if hashlib.sha256(frozen_raw).hexdigest() != frozen_hash:
        fail('FROZEN_HASH_MISMATCH')
    b = frozen.get('production_bootstrap')
    required = {'version','root','generation','pair_manifest_path','pair_manifest_sha256','authority_path','transfer_owner','stores','schema_authority'}
    if not isinstance(b, dict) or set(b) != required or type(b['version']) is not int or b['version'] != 1:
        fail('PROFILE_VERSION')
    root = physical(str(root)); path = physical(str(path))
    if str(root) != b['root'] or path.parent != root or path.suffix != '.sqlite' or not root.is_dir():
        fail('ROOT_OR_TARGET_MISMATCH')
    if not isinstance(b['stores'], dict) or set(b['stores']) != {'core','ops'} or not isinstance(b['schema_authority'], dict) or set(b['schema_authority']) != {'core','ops'} or b['schema_authority'][role] != schema_authority:
        fail('SCHEMA_AUTHORITY_MISMATCH')
    if os.environ.get('DEV004_PAIRED_BINDING_REQUIRED') != '1' or os.environ.get('FLEX_DB_PAIR_ROLE') != 'transfer' or not b['transfer_owner'] or os.environ.get('FLEX_DB_PAIR_OWNER') != b['transfer_owner']:
        fail('EXCLUSIVE_TRANSFER_OWNER_REQUIRED')
    if os.environ.get('FLEX_DB_PAIR_MANIFEST') != b['pair_manifest_path'] or os.environ.get('FLEX_DB_PAIR_AUTHORITY') != b['authority_path']:
        fail('PAIR_AUTHORITY_BINDING_MISMATCH')
    pair, pair_raw = read(b['pair_manifest_path'], 8192)
    owner, owner_raw = read(b['authority_path'], 8192)
    if hashlib.sha256(pair_raw).hexdigest() != b['pair_manifest_sha256'] or pair.get('generation') != b['generation'] or pair.get('stores') != b['stores'] or pair.get('predecessor') is None:
        fail('PAIRED_GENERATION_MISMATCH')
    if owner.get('phase') != 'TRANSFER' or owner.get('owner') != b['transfer_owner'] or owner.get('transfer_complete') is not False:
        fail('BOOTSTRAP_PHASE_REQUIRED')
    from src.core.paired_binding_guard import validate_pair, database_identity
    for kind in ['core','ops']:
        record = b['stores'][kind]
        p = physical(record.get('path'))
        info = p.lstat()
        if p.parent != root or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or database_identity(str(p), b['generation'], kind) != record:
            fail('DATABASE_IDENTITY_MISMATCH')
    if str(path) != b['stores'][role]['path'] or b['stores']['core']['path'] == b['stores']['ops']['path']:
        fail('WRONG_DATABASE_ROLE')
    if validate_pair(write_path=str(path), purpose='write') != b['generation']:
        fail('GENERATION_NOT_CURRENT')
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    if soft == resource.RLIM_INFINITY or hard == resource.RLIM_INFINITY or soft > 64*1024*1024 or hard >= 500000000 or path.stat().st_size >= 64*1024*1024:
        fail('KERNEL_FILE_LIMIT_REQUIRED')
    space = os.statvfs(root)
    if space.f_bavail * space.f_frsize < 2_000_000_000 + 16*1024*1024:
        fail('DISK_FLOOR')
    if read(frozen_path,65536)[1] != frozen_raw or read(b['authority_path'],8192)[1] != owner_raw:
        fail('AUTHORITY_CHANGED')
    return b['generation']

def normalized(s):return ''.join(s.lower().replace('if not exists','').split())
def _schema_authority():
 return {'contract':hashlib.sha256(json.dumps([MEMBERSHIP,OBJECTS],sort_keys=True).encode()).hexdigest(),'owner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'watchtower_owner_sha256':hashlib.sha256(Path(__file__).with_name('dev004_watchtower_references.py').read_bytes()).hexdigest()}
def profile(c,root,allowed):
 original_root=Path(root).absolute();root=Path(root).resolve();path=next(r[2] for r in c.execute('PRAGMA database_list') if r[1]=='main');p=Path(path).absolute();soft,_=resource.getrlimit(resource.RLIMIT_FSIZE)
 production_profile=bool(os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST') or os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_SHA256'))
 if production_profile:
  if not allowed or c.execute('PRAGMA foreign_keys').fetchone()[0]!=1:raise ReferenceContractError('EXPLICIT_FK_PROFILE_REQUIRED')
  _production_profile(p,original_root,'ops',_schema_authority())
 elif (not allowed or not root.is_relative_to('/private/tmp') or not root.name.startswith('dev004-') or not p.is_relative_to(root) or p!=p.resolve() or soft==resource.RLIM_INFINITY or soft>MAX_FILE or p.stat().st_size>=MAX_FILE or c.execute('PRAGMA foreign_keys').fetchone()[0]!=1):raise ReferenceContractError('EXPLICIT_DISPOSABLE_FK_PROFILE_REQUIRED')
 space=os.statvfs(root)
 if space.f_bavail*space.f_frsize<2_000_000_000+16*1024*1024:raise ReferenceContractError('DISK_FLOOR')
def initialize(c,*,disposable_root,allow_disposable_v2=False):
 profile(c,disposable_root,allow_disposable_v2)
 for table in ['operators','operator_identity_events','confirmed_operation_matches']:
  if not c.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",(table,)).fetchone():raise ReferenceContractError('PARENT_SCHEMA_REQUIRED:'+table)
 old=c.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name='operator_launch_membership'").fetchone()
 c.execute('SAVEPOINT dev004_typed_reference_init')
 try:
  if old and normalized(old[0])!=normalized(MEMBERSHIP):
   columns=[r[1] for r in c.execute('PRAGMA table_info(operator_launch_membership)')]
   if columns!=['mint','operator_id','source_population_id','assigned_at','event_id'] or c.execute('SELECT 1 FROM operator_launch_membership LIMIT 1').fetchone():raise ReferenceContractError('ONLY_EMPTY_LEGACY_BOOTSTRAP_SUPPORTED')
   if c.execute("SELECT 1 FROM sqlite_schema WHERE name='operator_membership_reference_v1'").fetchone():raise ReferenceContractError('PARTIAL_CONTRACT_AMBIGUOUS')
   c.execute('DROP TABLE operator_launch_membership') # Empty DEV bootstrap only.
   old=None
  if old is None:c.execute(MEMBERSHIP)
  for name,sql in OBJECTS.items():
   actual=c.execute('SELECT sql FROM sqlite_schema WHERE name=?',(name,)).fetchone()
   if actual:
    if normalized(actual[0])!=normalized(sql):raise ReferenceContractError('INCOMPATIBLE_OBJECT:'+name)
   else:c.execute(sql)
  validate(c);c.execute('RELEASE dev004_typed_reference_init')
 except BaseException:
  c.execute('ROLLBACK TO dev004_typed_reference_init');c.execute('RELEASE dev004_typed_reference_init');raise
 return {'contract':'DEV004_TYPED_MEMBERSHIP_REFERENCE_V1','max_memberships':MAX_MEMBERSHIPS,'identity_events_created':0}
def validate(c):
 if os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST') or os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_SHA256'):
  path=next(r[2] for r in c.execute('PRAGMA database_list') if r[1]=='main');_production_profile(Path(path),Path(path).parent,'ops',_schema_authority())
 if c.execute('PRAGMA foreign_keys').fetchone()[0]!=1:raise ReferenceContractError('FOREIGN_KEYS_REQUIRED')
 if c.execute('SELECT 1 FROM operator_launch_membership LIMIT 1 OFFSET 50000').fetchone():raise ReferenceContractError('MEMBERSHIP_CAPACITY')
 row=c.execute('SELECT m.mint FROM operator_launch_membership m LEFT JOIN operator_membership_reference_v1 r ON r.mint=m.mint WHERE r.mint IS NULL OR r.operator_id<>m.operator_id OR r.legacy_event_id IS NOT m.event_id LIMIT 1').fetchone()
 if row:raise ReferenceContractError('TYPED_REFERENCE_INCOMPLETE')
 for t in ['operator_launch_membership','operator_membership_reference_v1']:
  if c.execute('PRAGMA foreign_key_check('+t+')').fetchone():raise ReferenceContractError('REFERENCE_FK_FAILED')
 return True
