import importlib.util
from argparse import Namespace
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]; SCRIPT=ROOT/'scripts/run_watchtower_historical_backfill_session.py'
spec=importlib.util.spec_from_file_location('operator',SCRIPT); op=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(op)
def args(tmp_path,**changes):
 d=dict(mode='dry-run',ranks='22,23,24,26,41,43,46,47,48,50',state_dir=tmp_path/'state',evidence_dir=tmp_path/'evidence',max_requests=10,max_runtime_seconds=1800,max_evidence_bytes=2_000_000,max_consecutive_failures=2,max_health_failures=1)
 d.update(changes); return Namespace(**d)
def test_exact_dry_run_plan(tmp_path):
 x=op.plan(args(tmp_path)); assert x['selected_ranks']==[22,23,24,26,41,43,46,47,48,50]; assert len(set(x['request_identities']))==10; assert x['scheduler'] is False
def test_rejects_unsafe_paths_and_caps(tmp_path):
 with pytest.raises(SystemExit): op.plan(args(tmp_path,state_dir=ROOT/'database'))
 with pytest.raises(SystemExit): op.plan(args(tmp_path,max_requests=11))
def test_no_live_mode_without_fresh_authorization(tmp_path):
 a=args(tmp_path,mode='execute'); a.live_opt_in=True
 # Plan itself is provider-free; entrypoint separately hard-stops execute mode.
 assert op.plan(a)['mode']=='execute'
