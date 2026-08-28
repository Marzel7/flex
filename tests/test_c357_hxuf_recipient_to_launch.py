import json
from pathlib import Path
def test_recipient_cohort_and_safe_negative_result():
 x=json.loads((Path(__file__).parents[1]/'docs/audits/c357_hxuf_recipient_to_launch.v1.json').read_text())
 assert x['frozen_recipient_count']==6
 assert x['conclusion']['post_25_aug_exact_c357_matches']==0
 assert x['conclusion']['tracking_loss']=='NOT_PROVEN'
 assert x['safety']['workflow']=='PAUSED'
