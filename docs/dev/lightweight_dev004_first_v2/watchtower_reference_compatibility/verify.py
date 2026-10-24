"""Exact source preservation and shared-preflight result verification; no import."""
import hashlib,json,os,resource,sqlite3,subprocess,sys,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def main(preflight,synthetic):
 resource.setrlimit(resource.RLIMIT_FSIZE,(8_000_000,8_000_000))
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.create_connection=deny
 old=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/seed_reference_preflight/findings.json').read_text());r=json.loads((preflight/'result.json').read_text());findings=json.loads((preflight/'findings.json').read_text());tests=json.loads((synthetic/'checks.json').read_text())
 assert r['status']=='PASS' and r['total_selected_rows']==r['selected_rows_with_source_authority']==28462 and r['total_reference_edges_checked']==r['resolved_reference_edges']==3635 and r['unresolved_reference_count']==0 and findings==[]
 assert r['typed_reference_branches']=={'CONFIRMED_MATCH':180,'FUNDING_SIGNATURE':14,'IDENTITY_EVENT':111,'MEMBERSHIP_COMMIT':104,'NONE':762};assert r['qualified_exclusions']['new_exclusions']==0
 c=sqlite3.connect(Path('/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');checks={x['mint']:x for x in r['watchtower_extension_checks']};assert len(checks)==len(old)==118;retained=[]
 for before in old:
  mint=before['key']['mint'];now=checks[mint];assert now['membership_sha256']==before['row_sha256'] and now['event_id']==before['event_id'];o=dict(c.execute('SELECT * FROM wt_attribution_outcomes WHERE mint=?',(mint,)).fetchone());q=dict(c.execute('SELECT * FROM wt_walkback_queue WHERE mint=?',(mint,)).fetchone());member=dict(c.execute('SELECT * FROM operator_launch_membership WHERE mint=?',(mint,)).fetchone());assert digest(o)==now['outcome_sha256'] and digest(q)==now['queue_sha256'] and digest(member)==now['membership_sha256'],'SOURCE_DRIFT_REQUIRES_REVALIDATION'
  assert o['outcome_type']==before['operational_state']['outcome_type'] and o['confidence']==before['operational_state']['outcome_confidence'] and o['operator_id']==before['operational_state']['outcome_operator_id'];assert q['status']==before['operational_state']['queue_status'] and q['intelligence_outcome']==before['operational_state']['intelligence_outcome']
  item={'mint':mint,'event_id':now['event_id'],'reference_kind':now['decision']['kind'],'membership_sha256':digest(member),'outcome_sha256':digest(o),'queue_sha256':digest(q),'outcome_type':o['outcome_type'],'outcome_operator_id':o['operator_id'],'outcome_confidence':o['confidence'],'queue_intelligence_outcome':q['intelligence_outcome']}
  if now['decision']['kind']=='MEMBERSHIP_COMMIT':
   box=dict(c.execute('SELECT * FROM monitor_admission_outbox WHERE membership_id=?',(now['event_id'],)).fetchone());assert box['state']==now['decision']['outbox_state']==before['legacy_namespace']['outbox_state'];assert [box[k] for k in ['event_id','membership_id','operation_id','mint','committed_at']]==now['decision']['parent_values'];item.update(outbox_state=box['state'],outbox_event_id=box['event_id'],outbox_sha256=digest(box))
  retained.append(item)
 c.close();conflicts=[x['mint'] for x in retained if x['outcome_type']=='UNKNOWN_INFRASTRUCTURE'];pending=[x['mint'] for x in retained if x.get('outbox_state')=='PENDING'];assert len(conflicts)==10 and len(pending)==5
 unchanged={}
 for path in ['runtime/'+p+'/src/ops/dev004_membership_references.py' for p in ['listener','creator_resolution','walkback']]+['runtime/creator_resolution/src/ops/dev004_core_base_schema.py']:
  current=(ROOT/path).read_bytes();assert current==subprocess.check_output(['git','show','95f3a891:'+path],cwd=ROOT);unchanged[path]=hashlib.sha256(current).hexdigest()
 package_hashes={p:hashlib.sha256((ROOT/'runtime'/p/'src/ops/dev004_watchtower_references.py').read_bytes()).hexdigest() for p in ['listener','creator_resolution','walkback']};assert len(set(package_hashes.values()))==1
 assert tests['groups_passed']==26;v=os.statvfs('/private/tmp');assert v.f_bavail*v.f_frsize>=2_000_000_000
 result={'DEV004_STATUS':'PASS','REFERENCE_COMPATIBILITY':'PASS','SUPPORTED_REFERENCE_EDGES':3635,'UNRESOLVED_REFERENCE_COUNT':0,'MISSING_PARENT_COUNT':0,'SELECTED_ROWS':28462,'UNKNOWN_INFRASTRUCTURE_CONFLICTS_RETAINED':10,'PENDING_OUTBOX_ADMISSIONS_RETAINED':5,'BYZANTINE_PRICE_REGRESSION':'PASS; unchanged accepted source bytes and original branches/selected real-parent preflight','IMPORT_EXECUTED':'NO','PRODUCTION_MUTATIONS':0,'PROVIDER_CALLS':0,'NEW_OPERATIONAL_EXCLUSIONS':0,'SYNTHETIC_CONTROL_GROUPS_PASSED':26,'NEXT_ACTION':'NONE — requested reference compatibility qualification complete. Full disposable migration requires a separate next task; do not execute it here.','new_profile_package_sha256':package_hashes,'unchanged_accepted_source_sha256':unchanged,'conflicting_outcome_mints':conflicts,'pending_admission_mints':pending,'records':retained,'free_bytes_at_verification':v.f_bavail*v.f_frsize,'runtime_deployment_status':'QUALIFIED_NOT_DEPLOYED; isolated DEV tests only, no production authorization','qualification_scope':'Read-only full selected-population preflight plus synthetic-only FK/transaction/restart controls; no selected V1 records imported into a V2 database.'}
 print(json.dumps(result,indent=2,sort_keys=True))
if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
