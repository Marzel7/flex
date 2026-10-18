import copy,importlib.util,json,pathlib,sys,time,pytest
P=pathlib.Path(__file__).resolve().parents[1]/'tools/flex_dev.py'
spec=importlib.util.spec_from_file_location('dev080_health_tool',P);f=importlib.util.module_from_spec(spec);sys.modules[spec.name]=f;spec.loader.exec_module(f)
def api(state='UNKNOWN',cap=None,abnormal=False):
 cap=cap or state
 return {'status':state,'ts':int(time.time()),'capabilities':{'live_ingestion':{'status':cap,'signals':[{'name':'heartbeat_freshness','abnormal':abnormal}],'evidence':{'total':1,'abnormal':int(abnormal)}}},'incidents':[]}
def critical():
 a=api('CRITICAL',abnormal=True);a['incidents']=[{'severity':'CRITICAL','capability':'live_ingestion','title':'Live ingestion unavailable'}];return a
@pytest.mark.parametrize('data,text',[(api(),'System Health Unknown'),(api('HEALTHY'),'System Healthy'),(critical(),'Critical — Live ingestion unavailable'),(api('WARNING',abnormal=True),'System Warning'),(api('UNKNOWN'),'System Health Unknown Platform Infrastructure')])
def test_expected_states(data,text):assert f.validate_health_browser(data,text)['status']==data['status']
def test_stale_heartbeat_is_visible_warning():
 a=api('WARNING',abnormal=True);a['incidents']=[{'severity':'WARNING','capability':'live_ingestion','title':'Ingestion heartbeat stale'}]
 assert f.validate_health_browser(a,'Warning — Ingestion heartbeat stale')['status']=='WARNING'
def test_isolated_without_live_ingestion_preserves_retained_critical():
 result=f.validate_health_browser(critical(),'Critical — Live ingestion unavailable')
 assert result['isolated_live_acquisition'] is False and result['status']=='CRITICAL'
@pytest.mark.parametrize('data,text',[(critical(),'System Healthy'),(critical(),'System Health Unknown'),(api('UNKNOWN'),'System Healthy'),(api('HEALTHY'),'System Health Unknown'),({},'System Healthy'),(api('HEALTHY',cap='UNKNOWN'),'System Healthy'),(api('HEALTHY',abnormal=True),'System Healthy'),(critical(),'System Healthy Critical — Live ingestion unavailable')])
def test_false_states_fail_closed(data,text):
 with pytest.raises(f.Hold):f.validate_health_browser(data,text)
def test_missing_live_evidence_is_not_healthy():
 a=api('HEALTHY');a['capabilities']['live_ingestion']['evidence']={}
 with pytest.raises(f.Hold):f.validate_health_browser(a,'System Healthy')
def test_missing_health_response_is_not_unknown_fallback():
 with pytest.raises(f.Hold):f.validate_health_browser(None,'System Health Unknown')
def test_missing_observation_timestamp_rejected():
 a=api();del a['ts']
 with pytest.raises(f.Hold):f.validate_health_browser(a,'System Health Unknown')
def test_critical_api_cannot_hide_critical_capability():
 a=critical();a['status']='HEALTHY'
 with pytest.raises(f.Hold):f.validate_health_browser(a,'System Healthy')
def test_incident_hierarchy_preserved():
 a=critical();a['incidents'].insert(0,{'severity':'CRITICAL','capability':'infrastructure','title':'Database failure'})
 assert f.validate_health_browser(a,'Critical — Live ingestion unavailable')['expected_banner']=='Critical — Live ingestion unavailable'
 with pytest.raises(f.Hold):f.validate_health_browser(a,'Critical — Database failure')
def browser_fixture(root):
 receipt=root/'receipt';receipt.mkdir();hp=receipt/'health-api.json';hp.write_text(json.dumps(critical()))
 session={'source_sha':'a'*40,'run_id':'unit-fixture','port':8180,'receipt_dir':str(receipt),'health_api':{'path':str(hp),'sha256':f.digest(hp),'url':'http://127.0.0.1:8180/api/health/full','source_sha':'a'*40,'run_id':'unit-fixture'}}
 features={}
 for name,terms in f.FEATURES.items():
  text=' '.join(terms)
  if name=='core_health':text+=' Critical — Live ingestion unavailable'
  if name=='mission_control':text+=' Active Operators 3 known actors Inbox 0 active items'
  dom=root/(name+'.txt');dom.write_text(text);shot=root/(name+'.png');shot.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*512)
  features[name]={'url':'http://127.0.0.1:8180'+f.ROUTES[name],'dom':dom.name,'screenshot':shot.name}
 doc={'source_sha':'a'*40,'run_id':'unit-fixture','status':'PASS','features':features};return doc,session,hp
def test_browser_requires_same_session_tool_snapshot(tmp_path):
 doc,session,hp=browser_fixture(tmp_path);assert f.validate_browser(doc,session,tmp_path)['core_health']['health']['status']=='CRITICAL'
 session['health_api']['run_id']='different'
 with pytest.raises(f.Hold,match='session'):f.validate_browser(doc,session,tmp_path)
def test_browser_rejects_modified_api_evidence(tmp_path):
 doc,session,hp=browser_fixture(tmp_path);hp.write_text(json.dumps(api('HEALTHY')))
 with pytest.raises(f.Hold,match='evidence changed'):f.validate_browser(doc,session,tmp_path)
def test_browser_rejects_wrong_api_url(tmp_path):
 doc,session,hp=browser_fixture(tmp_path);session['health_api']['url']='http://127.0.0.1:8100/api/health/full'
 with pytest.raises(f.Hold,match='URL'):f.validate_browser(doc,session,tmp_path)
def test_browser_rejects_missing_tool_snapshot(tmp_path):
 doc,session,hp=browser_fixture(tmp_path);session.pop('health_api')
 with pytest.raises(f.Hold,match='tool-captured'):f.validate_browser(doc,session,tmp_path)
def test_health_response_hash_stays_checked_after_acceptance(tmp_path):
 doc,session,hp=browser_fixture(tmp_path);accepted=f.validate_browser(doc,session,tmp_path);hp.write_text(json.dumps(api('UNKNOWN')))
 with pytest.raises(f.Hold,match='Health API evidence changed'):f.check_browser_files({'features':accepted})

def test_preserved_control_corpus_is_exact_and_executable():
    import hashlib,unittest
    records=json.loads((P.parents[1]/'docs/audits/dev080/preserved-tooling-controls.json').read_text())
    count=0
    for index,(name,expected) in enumerate(records.items()):
        path=P.parents[1]/'tests'/name
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
        cs=importlib.util.spec_from_file_location('dev080_preserved_controls_'+str(index),path)
        module=importlib.util.module_from_spec(cs);sys.modules[cs.name]=module;cs.loader.exec_module(module)
        for cls in vars(module).values():
            if isinstance(cls,type) and issubclass(cls,unittest.TestCase):count+=len(unittest.defaultTestLoader.getTestCaseNames(cls))
    assert count==34  # Original release, accepted Node and archival controls; unchanged assertions.

def test_health_snapshot_preserved_without_candidate_creation(tmp_path):
    doc,session,hp=browser_fixture(tmp_path)
    item={'health_api_path':str(hp),'health_api_sha256':f.digest(hp)}
    dest=tmp_path/'preserved-health.api.json'
    assert f.preserve_health_snapshot(item,dest)==item['health_api_sha256'] and dest.read_bytes()==hp.read_bytes()
    with pytest.raises(f.Hold,match='already exists'):f.preserve_health_snapshot(item,dest)
    hp.write_text(json.dumps(api('UNKNOWN')))
    with pytest.raises(f.Hold,match='changed'):f.preserve_health_snapshot(item,tmp_path/'must-not-exist.json')
    assert not (tmp_path/'must-not-exist.json').exists()
