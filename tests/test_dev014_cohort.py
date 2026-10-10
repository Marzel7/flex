import json
from pathlib import Path
import pytest
from src.ops.watchtower_historical_cohort import manifests
from src.ops.watchtower_historical_backfill_controller import HistoricalBackfillController
ROOT=Path(__file__).resolve().parents[1]
def test_cohort_manifests_are_bounded_and_explicit(tmp_path):
 r=json.loads((ROOT/'docs/audits/dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json').read_text());p=json.loads((ROOT/'docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json').read_text())
 c=HistoricalBackfillController(tmp_path/'state',r,p)
 with pytest.raises(ValueError): manifests(c,tmp_path/'journal','')
 out=manifests(c,tmp_path/'journal','fixture')
 assert all(len(x['records'])<=50 and x['content_hash'] for x in out)
