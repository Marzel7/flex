import json,sqlite3
from pathlib import Path
R=Path(__file__).resolve().parents[1];P='777211c3-211e-551b-9310-ff9301570627';S='p3r-subtype-03f916dfa97fb93a4b9c'
def test_c357_subtype_registration_invariants():
 c=sqlite3.connect(R/'database/wt_ops_v2.db');q=json.loads((R/'docs/audits/c357_operation_qualification_review.v1.json').read_text());allow={x['mint'] for x in q['population']['supported_launches']}
 got={x[0] for x in c.execute('select mint from operator_subtype_projection where subtype_id=?',(S,))};primary={x[0] for x in c.execute('select mint from operator_launch_membership where operator_id=?',(P,))}
 assert len(primary)==109 and got==allow and len(got&primary)==50 and len(got-primary)==6
 assert c.execute('select automation_state,monitoring_mode from operator_subtypes where subtype_id=?',(S,)).fetchone()==('OFF','SHADOW_ONLY')
 assert c.execute('select count(*) from operator_subtype_projection where subtype_id=?',(S,)).fetchone()[0]==56
