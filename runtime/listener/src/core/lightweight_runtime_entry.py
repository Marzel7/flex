"""Same-process V2 launch boundary: exact pair/auxiliary authority and OS file cap."""
import json,os,resource,runpy,signal,sys
MAX_FILE_BYTES=499_000_000
MODULES={'src.core.pumpfun_curve_listener','src.core.creator_resolution_worker','src.core.walkback_worker'}
def activate():
 if os.environ.get('DEV004_PAIRED_BINDING_REQUIRED')!='1':raise RuntimeError('V2_EXPLICIT_PAIR_REQUIRED')
 for key in ['DB_PATH','WT_OPS_DB_PATH','CREATOR_DB_PATH','TMPDIR','SQLITE_TMPDIR','FLEX_DB_AUXILIARY_MANIFEST']:
  value=os.environ.get(key)
  if not value or not os.path.isabs(value) or os.path.realpath(value)!=value:raise RuntimeError('V2_EXPLICIT_PHYSICAL_BINDING_REQUIRED:'+key)
 if os.environ['DB_PATH']==os.environ['WT_OPS_DB_PATH']:raise RuntimeError('V2_DISTINCT_STORES_REQUIRED')
 soft,hard=resource.getrlimit(resource.RLIMIT_FSIZE);limit=MAX_FILE_BYTES if hard==resource.RLIM_INFINITY else min(MAX_FILE_BYTES,hard)
 if limit<=0:raise RuntimeError('V2_FILE_LIMIT_UNAVAILABLE')
 signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit))
 from src.core.paired_binding_guard import validate_pair
 generation=validate_pair(purpose='active');assert generation is not None
 # Retirement opt-in must not leave inherited aliases pointing at V1.
 if os.environ.get('FLEX_DB_PREDECESSOR_RETIREMENT_MANIFEST'):
  os.environ.update(FLEX_DB_PATH=os.environ['DB_PATH'],OPS_V2_DB_PATH=os.environ['WT_OPS_DB_PATH'])
 if validate_pair(write_path=os.environ['CREATOR_DB_PATH'],purpose='write')!=generation:raise RuntimeError('V2_AUXILIARY_GENERATION_MISMATCH')
 return {'generation':generation,'kernel_single_file_limit':resource.getrlimit(resource.RLIMIT_FSIZE),'core':os.environ['DB_PATH'],'ops':os.environ['WT_OPS_DB_PATH'],'creator_repository':os.environ['CREATOR_DB_PATH']}
def main():
 if len(sys.argv)<2 or sys.argv[1] not in MODULES:raise RuntimeError('V2_COMPONENT_REQUIRED')
 module=sys.argv[1];state=activate()
 if '--check-only' in sys.argv[2:]:print(json.dumps(state));return
 sys.argv=[module]+sys.argv[2:];runpy.run_module(module,run_name='__main__',alter_sys=True)
if __name__=='__main__':main()
