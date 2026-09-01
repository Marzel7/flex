from pathlib import Path
def test_subtype_read_model_never_writes_primary_membership():
 s=(Path(__file__).resolve().parents[1]/'src/ops/operator_routes.py').read_text()
 assert '/subtypes/<subtype_id>' in s and 'operator_subtype_projection' in s
 assert 'INSERT INTO operator_launch_membership' not in s and 'UPDATE operator_launch_membership' not in s

def test_c357_legacy_potential_url_redirects_to_subtype_projection():
 s=(Path(__file__).resolve().parents[1]/'src/ops/operator_routes.py').read_text()
 assert 'if candidate_id == C357_CANDIDATE:' in s
 assert 'f"/intelligence/operator/{C357_PARENT_OPERATOR}/subtypes/{C357_SUBTYPE_ID}"' in s

def test_c357_is_not_listed_as_a_living_potential_operation():
 s=(Path(__file__).resolve().parents[1]/'src/ops/operator_routes.py').read_text()
 assert 'if candidate_id == C357_CANDIDATE:\n                continue' in s

def test_detached_c357_subtype_is_hidden_from_the_leviathan_registry_row():
 t=(Path(__file__).resolve().parents[1]/'templates/operators_index.html').read_text()
 assert '.registry-subtype{display:none!important}' in t

def test_behaviour_detail_exposes_attribution_boundaries_without_internal_terms():
 t=(Path(__file__).resolve().parents[1]/'templates/operator_subtype_detail.html').read_text()
 for text in ('Leviathan','Similar behaviour alone does not prove Leviathan attribution.','Leviathan member','Monitoring: Shadow only'):
  assert text in t
 for text in ('Parent operation: P3R','P3R Primary','Projection Only','Supported subtype projection'):
  assert text not in t
