"""Bounded validation of the existing frozen Core/OPS launch manifest.

No authority publisher, daemon, queue, schema or distributed transaction.
The migration owner publishes configuration; consumers only validate it.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

CAP = 8192
SEAL_CAP = 2048
_CACHE = None  # One bounded validation result; metadata fingerprints invalidate it.

class PairedBindingError(RuntimeError):
    pass

def _fail(code):
    raise PairedBindingError(code)

def _path(value):
    if not isinstance(value,str) or not value or not os.path.isabs(value) or os.path.realpath(value)!=value:
        _fail('PAIR_ABSOLUTE_PHYSICAL_PATH_REQUIRED')
    return value

def _fingerprint(path):
    try:
        s=os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _fail('PAIR_METADATA_UNAVAILABLE')
    if stat.S_ISLNK(s.st_mode) or not stat.S_ISREG(s.st_mode):
        _fail('PAIR_REGULAR_FILE_REQUIRED')
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def _read(path,cap=CAP):
    before=_fingerprint(path)
    if before is None:_fail('PAIR_METADATA_MISSING')
    if before[2]>cap:_fail('PAIR_METADATA_CAPACITY')
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:raw=stream.read(cap+1)
        if len(raw)>cap:_fail('PAIR_METADATA_CAPACITY')
        value=json.loads(raw)
    except PairedBindingError:raise
    except Exception:_fail('PAIR_METADATA_INVALID')
    if _fingerprint(path)!=before:_fail('PAIR_METADATA_CHANGED')
    if not isinstance(value,dict):_fail('PAIR_METADATA_INVALID')
    return value,raw

def database_identity(path,generation,role):
    path=_path(str(path));fingerprint=_fingerprint(path)
    if fingerprint is None:_fail('PAIR_DATABASE_MISSING')
    return {'path':path,'device':fingerprint[0],'inode':fingerprint[1],'generation':generation,'role':role}

def _identity(record,generation,role):
    if not isinstance(record,dict) or set(record)!={'path','device','inode','generation','role'} or record.get('generation')!=generation or record.get('role')!=role:
        _fail('PAIR_GENERATION_IDENTITY')
    path=_path(record['path']);actual=_fingerprint(path)
    if actual is None or type(record['device']) is not int or type(record['inode']) is not int or (actual[0],actual[1])!=(record['device'],record['inode']):
        _fail('PAIR_DATABASE_IDENTITY_MISMATCH')
    return path

def _manifest(path):
    manifest,raw=_read(_path(path))
    if set(manifest)!={'version','authority','generation','stores','predecessor'} or type(manifest['version']) is not int or manifest['version']!=1:
        _fail('PAIR_MANIFEST_VERSION')
    generation=manifest['generation']
    if not isinstance(generation,str) or not 1<=len(generation.encode())<=128:_fail('PAIR_GENERATION_REQUIRED')
    authority=manifest['authority']
    if not isinstance(authority,dict) or set(authority)!={'id','path','origin_generation'} or not isinstance(authority['id'],str) or not 1<=len(authority['id'])<=128 or not isinstance(authority['origin_generation'],str):
        _fail('PAIR_AUTHORITY_REQUIRED')
    _path(authority['path'])
    if not isinstance(manifest['stores'],dict) or set(manifest['stores'])!={'core','ops'}:_fail('PAIR_STORES_REQUIRED')
    paths={role:_identity(manifest['stores'][role],generation,role) for role in ['core','ops']}
    if paths['core']==paths['ops']:_fail('PAIR_DISTINCT_DATABASES_REQUIRED')
    previous=manifest['predecessor']
    if previous is None:
        if generation!=authority['origin_generation']:_fail('PAIR_PREDECESSOR_REQUIRED')
    else:
        if not isinstance(previous,dict) or set(previous)!={'generation','stores'} or not isinstance(previous['generation'],str) or previous['generation']==generation or not isinstance(previous['stores'],dict) or set(previous['stores'])!={'core','ops'}:
            _fail('PAIR_PREDECESSOR_INVALID')
        for role in ['core','ops']:
            old=_identity(previous['stores'][role],previous['generation'],role)
            if old in paths.values():_fail('PAIR_PREDECESSOR_NOT_DISTINCT')
    return manifest,raw,paths

def _seal_value(manifest,path,raw,role):
    identity=manifest['stores'][role]
    return {'version':1,'authority_id':manifest['authority']['id'],'authority_path':manifest['authority']['path'],'manifest_path':path,'manifest_sha256':hashlib.sha256(raw).hexdigest(),'generation':manifest['generation'],'role':role,'device':identity['device'],'inode':identity['inode']}

def seal_manifest(path):
    """Explicit, bounded metadata registration; never called by normal startup.

    Does not publish/activate authority or retire stores. A partial seal cannot
    authorize runtime writes. Existing seals are immutable and must match.
    """
    path=_path(str(path));manifest,raw,paths=_manifest(path)
    for role in ['core','ops']:
        target=paths[role]+'.pair-bound';expected=_seal_value(manifest,path,raw,role)
        if os.path.lexists(target):
            if _read(target,SEAL_CAP)[0]!=expected:_fail('PAIR_SEAL_CONFLICT')
            # A previous fsync/dir-sync may have failed after readable bytes
            # appeared. An idempotent retry must establish durability again.
            fd=os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            try:os.fsync(fd)
            finally:os.close(fd)
            directory=os.open(str(Path(target).parent),os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            continue
        data=json.dumps(expected,sort_keys=True,separators=(',',':')).encode()
        if len(data)>SEAL_CAP:_fail('PAIR_SEAL_CAPACITY')
        space=os.statvfs(str(Path(target).parent))
        if space.f_bavail*space.f_frsize<2_000_000_000+SEAL_CAP:_fail('PAIR_SEAL_DISK_FLOOR')
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.write(fd,data)!=len(data):_fail('PAIR_SEAL_SHORT_WRITE')
            os.fsync(fd)
        finally:os.close(fd)
        directory=os.open(str(Path(target).parent),os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    return manifest

def validate_pair(*,core_path=None,ops_path=None,write_path=None,purpose='active'):
    """Legacy unbound V1 is unchanged; sealed/explicit paired mode is mandatory.

    TRANSFER permits only the declared offline transfer owner, never runtime
    startup or acknowledgements. ACTIVE always requires both complete seals.
    """
    global _CACHE
    core=os.path.realpath(str(core_path or os.environ.get('DB_PATH') or os.environ.get('FLEX_DB_PATH') or ''))
    ops=os.path.realpath(str(ops_path or os.environ.get('WT_OPS_DB_PATH') or ''))
    target=os.path.realpath(str(write_path)) if write_path else None
    required=os.environ.get('DEV004_PAIRED_BINDING_REQUIRED','0')=='1' or bool(os.environ.get('FLEX_DB_PAIR_MANIFEST')) or bool(os.environ.get('FLEX_DB_PAIR_AUTHORITY'))
    for path in {core,ops,target}-{None}:
        try:
            if os.lstat(path+'.pair-bound'):required=True
        except FileNotFoundError:pass
        except OSError:_fail('PAIR_SEAL_UNAVAILABLE')
    if not required:return None
    if not os.environ.get('DB_PATH') or not os.environ.get('WT_OPS_DB_PATH'):_fail('PAIR_EXPLICIT_BINDINGS_REQUIRED')
    manifest_path=_path(os.environ.get('FLEX_DB_PAIR_MANIFEST'))
    authority_path=_path(os.environ.get('FLEX_DB_PAIR_AUTHORITY'))
    # Fingerprint all current/predecessor metadata, not filenames alone. Bound
    # cache includes role, environment, inode and replacement/corruption state.
    preliminary,raw,paths=_manifest(manifest_path)
    tracked=[manifest_path,authority_path]
    for role in ['core','ops']:
        tracked += [paths[role],paths[role]+'.pair-bound',paths[role]+'.retired']
    previous=preliminary['predecessor']
    if previous:
        for role in ['core','ops']:
            old=previous['stores'][role]['path'];tracked += [old,old+'.retired']
    key=(core,ops,target,purpose,os.environ.get('FLEX_DB_PAIR_ROLE','runtime'),os.environ.get('FLEX_DB_PAIR_OWNER'),tuple((p,_fingerprint(p)) for p in tracked))
    if _CACHE is not None and _CACHE[0]==key:return _CACHE[1]
    manifest=preliminary
    if core!=paths['core'] or ops!=paths['ops']:_fail('PAIR_BINDING_MISMATCH')
    if target is not None and target not in paths.values():_fail('PAIR_WRITE_TARGET_MISMATCH')
    if authority_path!=manifest['authority']['path']:_fail('PAIR_AUTHORITY_PATH_MISMATCH')
    state,state_raw=_read(authority_path)
    allowed={'version','authority_id','origin_generation','generation','manifest_path','manifest_sha256','phase','owner','transfer_complete'}
    if set(state)!=allowed or type(state['version']) is not int or state['version']!=1 or state['authority_id']!=manifest['authority']['id'] or state['origin_generation']!=manifest['authority']['origin_generation']:
        _fail('PAIR_AUTHORITY_INVALID')
    if state['generation']!=manifest['generation'] or state['manifest_path']!=manifest_path or state['manifest_sha256']!=hashlib.sha256(raw).hexdigest():_fail('PAIR_AUTHORITY_NOT_CURRENT')
    phase=state['phase'];role=os.environ.get('FLEX_DB_PAIR_ROLE','runtime')
    if phase=='ACTIVE':
        if state['owner']!='runtime' or role!='runtime' or state['transfer_complete'] is not True:_fail('PAIR_ACTIVATION_INCOMPLETE')
    elif phase=='TRANSFER':
        if purpose!='write' or role!='transfer' or not state['owner'] or os.environ.get('FLEX_DB_PAIR_OWNER')!=state['owner'] or state['transfer_complete'] is not False:
            _fail('PAIR_TRANSFER_ONLY')
    else:_fail('PAIR_ACTIVATION_INCOMPLETE')
    for role in ['core','ops']:
        if os.path.lexists(paths[role]+'.retired'):_fail('PAIR_DATABASE_RETIRED')
        seal=paths[role]+'.pair-bound'
        if phase=='ACTIVE' or os.path.lexists(seal):
            if _read(seal,SEAL_CAP)[0]!=_seal_value(manifest,manifest_path,raw,role):_fail('PAIR_SEAL_MISMATCH')
    if previous:
        for role in ['core','ops']:
            old=previous['stores'][role]['path'];retired,_=_read(old+'.retired',2048)
            if set(retired)!={'version','path','generation','retired'} or type(retired['version']) is not int or retired['version']!=1 or retired['path']!=old or retired['generation']!=previous['generation'] or retired['retired'] is not True:
                _fail('PAIR_PREDECESSOR_NOT_RETIRED')
    if _read(authority_path)[1]!=state_raw:_fail('PAIR_AUTHORITY_CHANGED')
    _CACHE=(key,manifest['generation'])
    return manifest['generation']
