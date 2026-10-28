"""Bounded validation of the existing frozen Core/OPS launch manifest.

No daemon, queue, schema or distributed transaction. The existing manifest
owner may explicitly publish immutable predecessor retirement evidence;
consumers only validate checksum-pinned evidence and never infer retirement.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

CAP = 8192
SEAL_CAP = 2048
RETIREMENT_CAP = 32768
RETIREMENT_PATH_ENV = "FLEX_DB_PREDECESSOR_RETIREMENT_MANIFEST"
RETIREMENT_SHA_ENV = "FLEX_DB_PREDECESSOR_RETIREMENT_SHA256"
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

def _historical_identity(record,generation,role):
    if not isinstance(record,dict) or set(record)!={'path','device','inode','generation','role'} or record.get('generation')!=generation or record.get('role')!=role:
        _fail('PAIR_GENERATION_IDENTITY')
    path=_path(record['path'])
    if type(record['device']) is not int or record['device']<0 or type(record['inode']) is not int or record['inode']<=0:
        _fail('PAIR_GENERATION_IDENTITY')
    return path

def _identity(record,generation,role):
    path=_historical_identity(record,generation,role);actual=_fingerprint(path)
    if actual is None or (actual[0],actual[1])!=(record['device'],record['inode']):
        _fail('PAIR_DATABASE_IDENTITY_MISMATCH')
    return path


def _retired_marker(path,generation):
    value,raw=_read(path+'.retired',SEAL_CAP)
    if type(value.get('version')) is not int or value.get('retired') is not True or value!={'version':1,'path':path,'generation':generation,'retired':True}:
        _fail('PAIR_PREDECESSOR_NOT_RETIRED')
    return raw

def _retirement(manifest):
    """Explicit checksum-pinned authority, never a missing-file heuristic."""
    path=os.environ.get(RETIREMENT_PATH_ENV);digest=os.environ.get(RETIREMENT_SHA_ENV)
    if not path and not digest:return None,[]
    if not path or not digest or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        _fail('PAIR_RETIREMENT_BINDING_REQUIRED')
    path=_path(path)
    if path!=manifest['authority']['path']+'.predecessor-retirement-v1.json':
        _fail('PAIR_RETIREMENT_AUTHORITY_PATH')
    if os.path.lexists(path+'.stage'):_fail('PAIR_RETIREMENT_PUBLICATION_AMBIGUOUS')
    value,raw=_read(path,RETIREMENT_CAP)
    if hashlib.sha256(raw).hexdigest()!=digest:_fail('PAIR_RETIREMENT_CHECKSUM')
    if set(value)!={'version','contract','state','authority','predecessor','successor_manifest_path','successor_manifest_json','successor_manifest_sha256','retired_marker_sha256','successor_seal_sha256'} or type(value['version']) is not int or value['version']!=1 or value['contract']!='V1_PREDECESSOR_RETIREMENT_V1' or value['state']!='VERIFIED':
        _fail('PAIR_RETIREMENT_CONTRACT')
    if value['authority']!=manifest['authority']:_fail('PAIR_RETIREMENT_AUTHORITY_MISMATCH')
    previous=value['predecessor']
    if not isinstance(previous,dict) or set(previous)!={'generation','stores'} or previous['generation']!=manifest['authority']['origin_generation'] or not isinstance(previous.get('stores'),dict) or set(previous['stores'])!={'core','ops'}:
        _fail('PAIR_RETIREMENT_PREDECESSOR')
    try:source_raw=value['successor_manifest_json'].encode('utf-8');source=json.loads(source_raw)
    except Exception:_fail('PAIR_RETIREMENT_PROVENANCE')
    if not isinstance(source,dict) or len(source_raw)>CAP or hashlib.sha256(source_raw).hexdigest()!=value['successor_manifest_sha256'] or set(source)!={'version','authority','generation','stores','predecessor'} or type(source['version']) is not int or source['version']!=1 or not isinstance(source['generation'],str) or not 1<=len(source['generation'].encode())<=128 or source['authority']!=manifest['authority'] or source['predecessor']!=previous or source['generation']==previous['generation'] or not isinstance(source.get('stores'),dict) or set(source['stores'])!={'core','ops'}:
        _fail('PAIR_RETIREMENT_PROVENANCE')
    source_path=_path(value['successor_manifest_path'])
    if Path(source_path).parent!=Path(path).parent or not isinstance(value['retired_marker_sha256'],dict) or not isinstance(value['successor_seal_sha256'],dict) or set(value['retired_marker_sha256'])!={'core','ops'} or set(value['successor_seal_sha256'])!={'core','ops'}:
        _fail('PAIR_RETIREMENT_PROVENANCE')
    tracked=[path]
    for role in ['core','ops']:
        old=_historical_identity(previous['stores'][role],previous['generation'],role)
        current=_historical_identity(source['stores'][role],source['generation'],role)
        if old in [r['path'] for r in manifest['stores'].values()] or old==current:_fail('PAIR_PREDECESSOR_NOT_DISTINCT')
        marker=_retired_marker(old,previous['generation'])
        if hashlib.sha256(marker).hexdigest()!=value['retired_marker_sha256'][role]:_fail('PAIR_RETIREMENT_MARKER_CHANGED')
        actual=_fingerprint(old)
        if actual is not None and (actual[0],actual[1])!=(previous['stores'][role]['device'],previous['stores'][role]['inode']):_fail('PAIR_RETIRED_PREDECESSOR_REPLACED')
        seal,seal_raw=_read(current+'.pair-bound',SEAL_CAP)
        if seal!=_seal_value(source,source_path,source_raw,role) or hashlib.sha256(seal_raw).hexdigest()!=value['successor_seal_sha256'][role]:_fail('PAIR_RETIREMENT_PROVENANCE_SEAL')
        tracked += [old,old+'.retired',current+'.pair-bound']
    if previous['stores']['core']['path']==previous['stores']['ops']['path'] or source['stores']['core']['path']==source['stores']['ops']['path']:_fail('PAIR_RETIREMENT_PREDECESSOR')
    if manifest['predecessor'] and manifest['predecessor']['generation']==previous['generation']:
        if manifest['predecessor']!=previous or manifest!=source:_fail('PAIR_RETIREMENT_SUCCESSOR_MISMATCH')
    return value,tracked

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
        if not isinstance(previous,dict) or set(previous)!={'generation','stores'} or not isinstance(previous['generation'],str) or previous['generation']==generation or not isinstance(previous['stores'],dict) or not isinstance(previous.get('stores'),dict) or set(previous['stores'])!={'core','ops'}:
            _fail('PAIR_PREDECESSOR_INVALID')
        retirement,_=_retirement(manifest)
        certified=retirement is not None and previous==retirement['predecessor']
        for role in ['core','ops']:
            old=(_historical_identity if certified else _identity)(previous['stores'][role],previous['generation'],role)
            if old in paths.values():_fail('PAIR_PREDECESSOR_NOT_DISTINCT')
    if previous is None:_retirement(manifest)
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
    required=bool(os.environ.get(RETIREMENT_PATH_ENV)) or bool(os.environ.get(RETIREMENT_SHA_ENV)) or os.environ.get('DEV004_PAIRED_BINDING_REQUIRED','0')=='1' or bool(os.environ.get('FLEX_DB_PAIR_MANIFEST')) or bool(os.environ.get('FLEX_DB_PAIR_AUTHORITY'))
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
    retirement,retirement_paths=_retirement(preliminary)
    tracked=[manifest_path,authority_path]+retirement_paths
    for role in ['core','ops']:
        tracked += [paths[role],paths[role]+'.pair-bound',paths[role]+'.retired']
    previous=preliminary['predecessor']
    if previous:
        for role in ['core','ops']:
            old=previous['stores'][role]['path'];tracked += [old,old+'.retired']
    auxiliary_path = None
    if target is not None and target not in paths.values():
        # One accepted auxiliary writer, explicitly tied to this exact pair.
        # Unknown databases remain rejected; no filename-based fallback.
        if not os.environ.get('FLEX_DB_AUXILIARY_MANIFEST'):
            _fail('PAIR_WRITE_TARGET_MISMATCH')
        auxiliary_manifest = _path(os.environ.get('FLEX_DB_AUXILIARY_MANIFEST'))
        auxiliary, auxiliary_raw = _read(auxiliary_manifest)
        expected_hash = os.environ.get('FLEX_DB_AUXILIARY_SHA256')
        if not expected_hash or hashlib.sha256(auxiliary_raw).hexdigest() != expected_hash:
            _fail('PAIR_AUXILIARY_HASH_MISMATCH')
        if set(auxiliary) != {'version','generation','pair_manifest_sha256','stores'} or type(auxiliary['version']) is not int or auxiliary['version'] != 1 or auxiliary['generation'] != preliminary['generation'] or auxiliary['pair_manifest_sha256'] != hashlib.sha256(raw).hexdigest() or set(auxiliary['stores']) != {'creator_repository'}:
            _fail('PAIR_AUXILIARY_AUTHORITY_INVALID')
        auxiliary_path = _identity(auxiliary['stores']['creator_repository'], preliminary['generation'], 'creator_repository')
        if auxiliary_path in paths.values() or target != auxiliary_path:
            _fail('PAIR_WRITE_TARGET_MISMATCH')
        if os.path.lexists(auxiliary_path + '.retired'):
            _fail('PAIR_AUXILIARY_RETIRED')
        tracked += [auxiliary_manifest, auxiliary_path, auxiliary_path + '.retired']
    key=(os.environ.get(RETIREMENT_PATH_ENV),os.environ.get(RETIREMENT_SHA_ENV),core,ops,target,purpose,os.environ.get('FLEX_DB_PAIR_ROLE','runtime'),os.environ.get('FLEX_DB_PAIR_OWNER'),tuple((p,_fingerprint(p)) for p in tracked))
    if _CACHE is not None and _CACHE[0]==key:return _CACHE[1]
    manifest=preliminary
    if core!=paths['core'] or ops!=paths['ops']:_fail('PAIR_BINDING_MISMATCH')
    if target is not None and target not in list(paths.values()) + [auxiliary_path]:_fail('PAIR_WRITE_TARGET_MISMATCH')
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
            old=previous['stores'][role]['path'];_retired_marker(old,previous['generation'])
    if _read(authority_path)[1]!=state_raw:_fail('PAIR_AUTHORITY_CHANGED')
    _CACHE=(key,manifest['generation'])
    return manifest['generation']


def publish_predecessor_retirement(manifest_path,certificate_path,*,authorize=False,crash=None):
    """Existing pair owner: explicit write-once metadata, never deletes a DB.

    Run only at a separately authorized quiescent owner boundary. Both original
    files and their retirement markers must still be independently verifiable.
    Return a checksum only after fsync and independent destination readback.
    """
    if authorize is not True:_fail('PAIR_RETIREMENT_EXPLICIT_AUTHORIZATION_REQUIRED')
    if os.environ.get(RETIREMENT_PATH_ENV) or os.environ.get(RETIREMENT_SHA_ENV):_fail('PAIR_RETIREMENT_PUBLICATION_REQUIRES_ORIGINAL_BINDING')
    manifest_path=_path(str(manifest_path));path=_path(str(certificate_path))
    if manifest_path!=os.environ.get('FLEX_DB_PAIR_MANIFEST'):_fail('PAIR_RETIREMENT_SOURCE_BINDING')
    manifest,raw,paths=_manifest(manifest_path);previous=manifest['predecessor']
    if not previous or previous['generation']!=manifest['authority']['origin_generation']:_fail('PAIR_RETIREMENT_ORIGINAL_PREDECESSOR_REQUIRED')
    if path!=manifest['authority']['path']+'.predecessor-retirement-v1.json':_fail('PAIR_RETIREMENT_AUTHORITY_PATH')
    if validate_pair(purpose='active')!=manifest['generation']:_fail('PAIR_RETIREMENT_INACTIVE_SOURCE')
    seal_manifest(manifest_path)
    markers={};seals={}
    for role in ['core','ops']:
        old=_identity(previous['stores'][role],previous['generation'],role)
        markers[role]=hashlib.sha256(_retired_marker(old,previous['generation'])).hexdigest()
        fd=os.open(old+'.retired',os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:os.fsync(fd)
        finally:os.close(fd)
        directory=os.open(str(Path(old).parent),os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        seal,seal_raw=_read(paths[role]+'.pair-bound',SEAL_CAP)
        if seal!=_seal_value(manifest,manifest_path,raw,role):_fail('PAIR_SEAL_MISMATCH')
        seals[role]=hashlib.sha256(seal_raw).hexdigest()
    value={'version':1,'contract':'V1_PREDECESSOR_RETIREMENT_V1','state':'VERIFIED','authority':manifest['authority'],'predecessor':previous,'successor_manifest_path':manifest_path,'successor_manifest_json':raw.decode('utf-8'),'successor_manifest_sha256':hashlib.sha256(raw).hexdigest(),'retired_marker_sha256':markers,'successor_seal_sha256':seals}
    data=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(data)>RETIREMENT_CAP:_fail('PAIR_RETIREMENT_CAPACITY')
    space=os.statvfs(Path(path).parent)
    if space.f_bavail*space.f_frsize<2_000_000_000+64*1024*1024+RETIREMENT_CAP:_fail('PAIR_RETIREMENT_DISK_FLOOR')
    stage=path+'.stage'
    for p in [stage,path]:
        if os.path.lexists(p) and _read(p,RETIREMENT_CAP)[1]!=data:_fail('PAIR_RETIREMENT_PUBLICATION_CONFLICT')
    if not os.path.lexists(stage):
        fd=os.open(stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.write(fd,data)!=len(data):_fail('PAIR_RETIREMENT_SHORT_WRITE')
            os.fsync(fd)
        finally:os.close(fd)
    else:
        fd=os.open(stage,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:os.fsync(fd)
        finally:os.close(fd)
    if crash:crash('before_commit')
    # Revalidate complete source authority immediately before immutable publish.
    if _read(manifest_path)[1]!=raw or validate_pair(purpose='active')!=manifest['generation']:_fail('PAIR_RETIREMENT_SOURCE_CHANGED')
    if not os.path.lexists(path):os.link(stage,path,follow_symlinks=False)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:os.fsync(fd)
    finally:os.close(fd)
    directory=os.open(str(Path(path).parent),os.O_RDONLY)
    try:
        os.fsync(directory)
        if crash:crash('after_commit')
        if _read(path,RETIREMENT_CAP)[1]!=data:_fail('PAIR_RETIREMENT_READBACK')
        os.unlink(stage);os.fsync(directory)
    finally:os.close(directory)
    if _read(path,RETIREMENT_CAP)[1]!=data:_fail('PAIR_RETIREMENT_READBACK')
    return {'path':path,'sha256':hashlib.sha256(data).hexdigest(),'predecessor':previous,'state':'VERIFIED'}
