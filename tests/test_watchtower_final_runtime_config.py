import subprocess
from scripts.verify_watchtower_final_runtime import ROOT,verify
def test_config_has_no_secrets_or_dirty_runtime_paths():
 text=(ROOT/'config/watchtower_final_runtime.json').read_text();assert 'BIRDEYE=' not in text and 'HELIUS=' not in text and 'watchtower-two-attempt' not in text
def test_ledger_identity_and_runtime_modules():
    result=verify(subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip());assert result['worker']=='src.ops.operation_monitor_service' and result['product']=='src.dev_runtime'

def test_supervisor_definitions_use_only_runtime_root_and_authority_guards():
    text=(ROOT/'config/supervisor/watchtower_final_runtime.conf').read_text(); wrapper=(ROOT/'scripts/launch_watchtower_final.sh').read_text()
    assert 'WATCHTOWER_FINAL_ROOT' in text and 'watchtower_listener' not in text
    assert 'verify_watchtower_final_runtime.py' in wrapper and 'gunicorn' in wrapper and 'watchtower-two-attempt' not in wrapper
