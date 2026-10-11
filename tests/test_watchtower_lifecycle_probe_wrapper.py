import json, subprocess, sys
from pathlib import Path
from src.ops.watchtower_lifecycle_probe import request_identity
ROOT=Path(__file__).resolve().parents[1]
def test_fixture_runner_is_exactly_one_terminal_request(tmp_path):
 a={'rank':49,'mint':'AqYdSQ6oFq8HjLoFkvHW6kYcRM1p94Q1FcvGFxDfpump','creation_timestamp':100,'time_from':100,'time_to':1000,'interval':'15m'}; a['content_hash']=request_identity(a)
 auth=tmp_path/'auth.json'; auth.write_text(json.dumps(a)); journal=tmp_path/'journal.json'
 result=subprocess.run([sys.executable,str(ROOT/'scripts/run_watchtower_lifecycle_probe.py'),'--authorization',str(auth),'--journal',str(journal),'--queue-root',str(tmp_path/'queue'),'--supervisor-config',str(tmp_path/'none'),'--test-fixture'],text=True,capture_output=True,check=False)
 assert result.returncode==0 and json.loads(result.stdout)['outcome']=='NO_VALID_CANDLES'
 again=subprocess.run([sys.executable,str(ROOT/'scripts/run_watchtower_lifecycle_probe.py'),'--authorization',str(auth),'--journal',str(journal),'--queue-root',str(tmp_path/'queue'),'--supervisor-config',str(tmp_path/'none'),'--test-fixture'],text=True,capture_output=True,check=False)
 assert again.returncode != 0 and 'ALREADY_ATTEMPTED' in again.stderr
