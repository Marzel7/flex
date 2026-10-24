"""Verify complete preflight accounting and existing retained-evidence digests only."""
import hashlib,json,sqlite3,sys
from pathlib import Path
base=Path(sys.argv[1]);result=json.loads((base/'result.json').read_text());findings=json.loads((base/'findings.json').read_text());checkpoint=json.loads((base/'checkpoint.json').read_text());groups=[]
def passed(name):groups.append(name)
assert hashlib.sha256(json.dumps(findings,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()==result['findings_sha256']==checkpoint['findings_sha256'];passed('findings_digest_matches_result_and_completion_checkpoint')
assert result['selected_rows_with_source_authority']==result['total_selected_rows']==28462;assert result['base_selected_rows']==28444 and sum(result['six_case_closure_additions'].values())==18;passed('complete_selected_population_accounting')
assert result['total_reference_edges_checked']==result['resolved_reference_edges']+len(findings)==3635;assert len(findings)==result['unique_affected_rows']==118;passed('complete_edge_accounting_no_early_exit')
assert len({json.dumps([f['database'],f['table'],f['key'],f['relationship']],sort_keys=True) for f in findings})==118;passed('no_duplicate_findings')
assert result['legacy_namespace_groups']=={'LEGACY_FUNDER_SIGNATURE_REFERENCE':14,'MEMBERSHIP_COMMIT_ID_WITH_DURABLE_OUTBOX':104};assert result['namespace_source_links_verified']==118;passed('all_legacy_namespace_source_links_recorded')
assert all(not any('SCAN ' in p for p in plan) for plan in result['lookup_plans'].values());passed('all_recorded_source_lookup_plans_indexed')
assert checkpoint['complete'] and checkpoint['phase']=='COMPLETE' and checkpoint['selected_rows']==28462 and checkpoint['reference_edges']==3635;passed('completion_checkpoint_population_and_edge_fencing')
assert result['typed_reference_branches']=={'CONFIRMED_MATCH':180,'IDENTITY_EVENT':111,'NONE':762};passed('existing_typed_compatibility_and_explicit_nulls_preserved')
assert len(json.loads((base/'excluded_controls.json').read_text()))==29 and result['qualified_exclusions']['new_exclusions']==0;passed('only_previous_documented_synthetic_exclusions')
ref=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure/ops_v2.sqlite');c=sqlite3.connect(ref.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;n=0
for row in c.execute('SELECT * FROM dev004_retained_reference_evidence LIMIT 16'):
 payload=json.loads(row['payload_json']);digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest();assert digest==row['row_sha256'];key=json.loads(row['source_key_json']);assert (row['source_table']=='audit_excerpt' and row['qualification'].startswith('SOURCE_FILE_SHA256=')) or all(payload[k]==v for k,v in key.items());n+=1
assert n==15;c.close();passed('15_external_payload_digests_13_row_keys_2_prior_qualified_audit_contexts')
assert all(result[x]==0 for x in ['production_mutations','provider_calls','target_databases_created','full_database_copies','corrections_applied','exclusions_applied']);passed('read_only_no_import_or_corrections')
output={'verification':'PASS','groups_passed':len(groups),'groups':groups,'external_retained_evidence_records_verified':n,'current_outcome_discrepancies':[f['key'] for f in findings if f.get('operational_state',{}).get('outcome_type')=='UNKNOWN_INFRASTRUCTURE'],'limitation':'Verification of recorded bounded preflight evidence; not runtime deployment or cutover acceptance.'}
assert len(output['current_outcome_discrepancies'])==10
print(json.dumps(output,indent=2,sort_keys=True))
