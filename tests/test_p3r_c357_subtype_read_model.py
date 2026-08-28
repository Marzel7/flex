from pathlib import Path
def test_subtype_read_model_never_writes_primary_membership():
 s=(Path(__file__).resolve().parents[1]/'src/ops/operator_routes.py').read_text()
 assert '/subtypes/<subtype_id>' in s and 'operator_subtype_projection' in s
 assert 'INSERT INTO operator_launch_membership' not in s and 'UPDATE operator_launch_membership' not in s
def test_subtype_detail_exposes_non_owning_boundaries():
 t=(Path(__file__).resolve().parents[1]/'templates/operator_subtype_detail.html').read_text()
 for text in ('Parent operation: P3R','Compatible ≠ Attributed','P3R Primary','Projection Only','Automatic attribution','Shadow monitoring'):
  assert text in t
