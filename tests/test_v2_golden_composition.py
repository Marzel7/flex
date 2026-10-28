"""Small, provider-free serialization/source authority controls."""
import copy,hashlib,importlib.util,json,pathlib,subprocess
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('golden_verify',ROOT/'scripts/flex_v2_golden_verify.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
def manifest():
 p=ROOT/'scripts/flex_v2_fresh_pair_recovery.py'
 return {'format':'FLEX_V2_GOLDEN_CANDIDATE_V1','source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'generation':'fixture','source_files':{'scripts/flex_v2_fresh_pair_recovery.py':{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}},'runtime_configuration':{'generation':'fixture','helius':{'investigation_lifetime_credits':100000},'recovery':{'walkback_interrupted_claim_state':'pending'},'retention':{'minimum_age_seconds':604800,'schedule_count':1}}}
@pytest.mark.parametrize('fault',['commit','hash','path','worktree','generation','credits','claim','age','schedule'])
def test_mixed_stale_and_incompatible_contracts_refused(fault):
 m=manifest()
 if fault=='commit':m['source_commit']='0'*40
 elif fault=='hash':m['source_files']['scripts/flex_v2_fresh_pair_recovery.py']['sha256']='0'*64
 elif fault=='path':m['source_files']={'../outside.py':{'bytes':0,'sha256':'0'*64}}
 elif fault=='worktree':m['runtime_configuration']['root']='/Users/example/.codex/worktrees/old/flex'
 elif fault=='generation':m['runtime_configuration']['generation']='wrong'
 elif fault=='credits':m['runtime_configuration']['helius']['investigation_lifetime_credits']=100001
 elif fault=='claim':m['runtime_configuration']['recovery']['walkback_interrupted_claim_state']='retry'
 elif fault=='age':m['runtime_configuration']['retention']['minimum_age_seconds']=604799
 else:m['runtime_configuration']['retention']['schedule_count']=2
 with pytest.raises(v.Hold):v.verify(ROOT,m)
def test_exact_installed_listener_maintenance_source():
 p=ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py'
 assert hashlib.sha256(p.read_bytes()).hexdigest()=='bdb3ea55b9162162885885f21766ed3515dad7ca7ba676b99d8fec17359f9986'
def test_recovery_pending_code_is_accepted_installed_source():
 p=ROOT/'scripts/flex_v2_fresh_pair_recovery.py'
 assert hashlib.sha256(p.read_bytes()).hexdigest()=='50967f4cd1109dd8cdae78acb629fa586b7ad2733e55b0bc5dd5e257871edcc5'

def test_valid_manifest_verifies_without_external_state_access():
 assert v.verify(ROOT,manifest())['status']=='SOURCE_CONFIGURATION_VERIFIED'
