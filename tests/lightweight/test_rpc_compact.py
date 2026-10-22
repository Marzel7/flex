"""Provider-free fixtures only. Never resolve a production database path."""
import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('compact',ROOT/'src/metrics/rpc_compact.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
spec=importlib.util.spec_from_file_location('retained_fixture',ROOT/'docs/dev/lightweight_dev002/qualify.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
MEASUREMENTS={}
NOW=1792704000.0  # 2026-10-22T21:20:00Z, fixed fixture clock

class Tests(unittest.TestCase):
 def db(self,rows=None,path=':memory:',limits=None):
  c=sqlite3.connect(path)
  c.execute(next(v['sql'] for v in f.META['schema'] if v['name']=='rpc_metrics'))
  c.execute('CREATE INDEX idx_rpc_metrics_timestamp ON rpc_metrics(timestamp DESC)')
  for v in f.META['schema']:
   if v['type']=='view':c.execute(v['sql'])
  for row in rows or []:self.insert(c,row)
  c.commit();s=m.CompactTelemetry(c,limits or m.Limits(seconds=1));s.install(NOW)
  self.addCleanup(c.close)
  return c,s
 def insert(self,c,row):
  c.execute('INSERT INTO rpc_metrics ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
 def catchup(self,s,now=NOW):
  for _ in range(2000):
   r=s.advance(now)
   if r['status']=='ok' and not r['processed']:return
  self.fail('backfill failed to converge')
 def expect(self,code,fn):
  with self.assertRaises(m.Hold) as e:fn()
  self.assertEqual(e.exception.code,code)
 def row(self,i=1,age=0,**kwargs):
  r=f.fixture()[0].copy();ts=NOW-age*86400;r.update(id=i,timestamp=ts,recorded_at=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(ts)))
  r.update(kwargs);return r
 def test_retained_22_equivalence_cases(self):
  # Eleven retained checks on each of two explicit fixture populations, no live sample.
  rows=f.fixture();other=[self.row(i+1,age=i%20,credits=i%11,credits_saved=i%7,section='s'+str(i%3)) for i in range(128)]
  for population in (rows,other):
   self.assertEqual(len(f.qualify(population)['exact_checks']),11)
   c,s=self.db(population);self.catchup(s)
   c.row_factory=sqlite3.Row
   for name in m.VIEWS:
    expected=[dict(r) for r in c.execute('SELECT * FROM '+name)]
    result=s.report(name,NOW)['data']
    if name not in m.LIFETIME_VIEWS:expected=[r for r in expected if r['day']>='2026-09-22']
    self.assertEqual(sorted(expected,key=repr),sorted(result,key=repr),name)
 def test_seven_day_and_unsupported_rolling(self):
  c,s=self.db([self.row(1,7),self.row(2,7,timestamp=NOW-7*86400+.001)])
  self.assertEqual(s.window(NOW,hours=168)['cutoff'],NOW-7*86400)
  self.assertEqual(c.execute('SELECT COUNT(*) FROM rpc_metrics WHERE timestamp>?',(s.window(NOW,hours=168)['cutoff'],)).fetchone()[0],1)
  for hours in (168.001,720,-1,float('nan')):self.expect('UNSUPPORTED_HISTORICAL_ROLLING_WINDOW',lambda:s.window(NOW,hours=hours))
 def test_thirty_day_labels_and_trend_seed(self):
  c,s=self.db([self.row(i+1,age=i) for i in range(45)])
  self.catchup(s)
  r=s.report('v_rpc_daily_savings',NOW)
  self.assertEqual(len(r['data']),31)
  self.assertEqual(c.execute("SELECT COUNT(*) FROM rpc_compact_cells WHERE scope='seed'").fetchone()[0],12)
  c.row_factory=sqlite3.Row
  raw=[dict(r) for r in c.execute("SELECT * FROM v_rpc_efficiency_trend_7day WHERE day>='2026-09-22'")]
  self.assertEqual(sorted(raw,key=repr),sorted(s.report('v_rpc_efficiency_trend_7day',NOW)['data'],key=repr))
 def test_lifetime_does_not_expire_and_days_are_distinct(self):
  c,s=self.db([self.row(1,180),self.row(2,180),self.row(3,3),self.row(4,0)])
  self.catchup(s);self.assertEqual(s.totals()['stats']['n'],4);self.assertEqual(s.totals()['days_tracked'],3)
  self.catchup(s,NOW+60*86400);self.assertEqual(s.totals()['stats']['n'],4)
  self.assertEqual(s.report('v_rpc_daily_savings',NOW+60*86400)['data'],[])
 def test_late_arrival_separate_clocks(self):
  c,s=self.db();self.catchup(s)
  self.insert(c,self.row(1,age=40,recorded_at='2026-10-22 21:00:00'));c.commit();self.catchup(s)
  self.assertEqual(s.totals()['stats']['n'],1)
  self.assertEqual(len(s.report('v_rpc_daily_savings',NOW)['data']),1)
  self.assertEqual(s.report('v_rpc_daily_savings',NOW,clock='timestamp')['data'],[])
 def test_retry_and_gaps_count_persisted_event_once(self):
  c,s=self.db([self.row(3),self.row(9),self.row(100)])
  self.catchup(s);self.catchup(s);self.assertEqual(s.totals()['stats']['n'],3)
  self.insert(c,self.row(101));c.commit();self.catchup(s);self.assertEqual(s.totals()['stats']['n'],4)
 def test_crash_rolls_back_cursor_and_counters(self):
  c,s=self.db([self.row(1),self.row(2)])
  def crash(i):raise RuntimeError('injected pre-commit crash')
  with self.assertRaises(RuntimeError):s.advance(NOW,crash=crash)
  self.assertEqual(s.meta()['cursor'],0);self.assertEqual(s.totals()['stats']['n'],0)
  self.catchup(s);self.assertEqual(s.totals()['stats']['n'],2)
 def test_restart_after_committed_slice(self):
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'fixture.db');c,s=self.db([self.row(i+1) for i in range(4)],path)
   s.advance(NOW,limit=2);c.commit();other=sqlite3.connect(path)
   try:
    s2=m.CompactTelemetry(other,m.Limits(seconds=1));self.catchup(s2);self.assertEqual(s2.totals()['stats']['n'],4)
   finally:other.close()
 def test_dimension_overflow_preserves_global_totals(self):
  c,s=self.db([self.row(i+1,section='s'+str(i)) for i in range(30)],limits=m.Limits(keys=8,seconds=1))
  self.catchup(s);tot=s.totals();self.assertTrue(tot['dimensions_overflow']);self.assertEqual(tot['stats']['n'],30)
  self.assertTrue(s.report('v_rpc_daily_savings',NOW)['dimensions_overflow'])
  self.assertLessEqual(c.execute("SELECT COUNT(*) FROM rpc_compact_cells WHERE clock='timestamp' AND scope='life'").fetchone()[0],8)
  self.assertEqual(c.execute("SELECT n FROM rpc_compact_counts WHERE clock='timestamp' AND scope='life'").fetchone()[0],c.execute("SELECT COUNT(*) FROM rpc_compact_cells WHERE clock='timestamp' AND scope='life'").fetchone()[0])
 def test_p95_explicitly_unsupported(self):
  c,s=self.db();self.expect('UNSUPPORTED_HISTORICAL_P95',lambda:s.window(NOW,start_day='2026-10-01',end_day='2026-10-02',p95=True))
 def test_pinned_forensic_rows_cannot_change_before_backfill(self):
  c,s=self.db([self.row(1,age=200)]);s.pin(1,'accepted audit fixture')
  with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM rpc_metrics WHERE id=1')
  self.catchup(s);self.assertEqual(c.execute('SELECT COUNT(*) FROM rpc_metrics').fetchone()[0],1)
  with self.assertRaises(sqlite3.IntegrityError):c.execute('UPDATE rpc_metrics SET credits=8 WHERE id=1')
 def test_reset_seed_external_baseline_and_late_rows(self):
  c,s=self.db([self.row(1,age=2,credits=10),self.row(2,age=0,credits=20)])
  boundary=NOW-86400;s.seed_reset('accepted',boundary,boundary+124,757471);self.catchup(s)
  before=s.reset_report('accepted',NOW,757500)
  self.assertEqual(before['local_credits_since_reset'],20);self.assertEqual(before['helius_credits_since_reset'],29)
  self.insert(c,self.row(3,age=3,credits=100));self.insert(c,self.row(4,age=.5,credits=5));c.commit();self.catchup(s)
  self.assertEqual(s.reset_report('accepted',NOW,757500)['local_credits_since_reset'],25)
  self.assertEqual(s.totals()['stats']['spent'],135)
  self.assertEqual(s.reset_totals('accepted')['elapsed_origin'],boundary+124)
 def test_new_reset_lifetime_not_reset(self):
  c,s=self.db([self.row(1,age=1)]);self.catchup(s);s.reset('manual',NOW,NOW,10,NOW)
  self.insert(c,self.row(2,timestamp=NOW+1));c.commit();self.catchup(s,NOW+2)
  self.assertEqual(s.reset_report('manual',NOW+2,20)['tracked_calls'],1)
  self.assertEqual(s.totals()['stats']['n'],2)
 def test_incomplete_reports_are_hold_not_partial(self):
  c,s=self.db([self.row(1)]);self.expect('BACKFILL_INCOMPLETE',lambda:s.report('v_rpc_cumulative_savings',NOW))
 def test_cost_and_byte_bounds_rollback(self):
  c,s=self.db([self.row(1)],limits=m.Limits(encoded_bytes=100,seconds=1))
  self.expect('AGGREGATE_BYTE_LIMIT',lambda:s.advance(NOW))
  self.assertEqual(s.meta()['cursor'],0);self.assertEqual(s.meta()['encoded_bytes'],0)
 def test_source_immutability_id_reuse_and_pin_budget(self):
  c,s=self.db([self.row(1)]);self.catchup(s)
  with self.assertRaises(sqlite3.IntegrityError):self.insert(c,self.row(0))
  with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM rpc_metrics WHERE id=1')
 def test_keyset_plan_and_bounds(self):
  c,s=self.db([self.row(1)])
  p=c.execute('EXPLAIN QUERY PLAN SELECT id FROM rpc_metrics WHERE id>? ORDER BY id LIMIT ?',(0,128)).fetchall()
  self.assertIn('INTEGER PRIMARY KEY',str(p));self.assertNotIn('SCAN rpc_metrics',str(p))
  with self.assertRaises(ValueError):s.advance(NOW,limit=129)
 def test_null_empty_and_integer_division(self):
  for rows in ([],[self.row(1,credits=None,credits_saved=None,recorded_at=None)],[self.row(1,credits=3,credits_saved=5)]):
   c,s=self.db(rows);self.catchup(s);c.row_factory=sqlite3.Row
   for name in m.LIFETIME_VIEWS:
    self.assertEqual([dict(r) for r in c.execute('SELECT * FROM '+name)],s.report(name,NOW)['data'],name)
 def test_wal_small_fixture_no_checkpoint_call(self):
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'wal.db');c,s=self.db([self.row(i+1,section='s'+str(i%4)) for i in range(256)],path)
   c.execute('PRAGMA journal_mode=WAL');reader=sqlite3.connect(path);reader.execute('BEGIN');reader.execute('SELECT * FROM rpc_compact_meta').fetchall()
   try:
    self.catchup(s);c.commit();wal=pathlib.Path(path+'-wal')
    MEASUREMENTS['held_reader_wal_bytes']=wal.stat().st_size
    self.assertLess(wal.stat().st_size,2*1024*1024)
    self.assertLess(pathlib.Path(path).stat().st_size,500_000_000)
   finally:reader.close()

 def api(self):
  import importlib
  sys.path.insert(0,str(ROOT));self.addCleanup(lambda:sys.path.remove(str(ROOT)))
  sys.modules['src.metrics.rpc_compact']=m
  return importlib.import_module('src.metrics.rpc_compact_api')
 def test_database_api_unweighted_source_latency_and_lifetime(self):
  api=self.api();rows=[self.row(1,section='a',latency_ms=10),self.row(2,section='b',latency_ms=30),self.row(3,section='b',latency_ms=30)]
  c,s=self.db(rows);self.catchup(s)
  r=api.database_daily(c,NOW,'2026-10-22','2026-10-22')
  self.assertEqual(next(iter(r['database_metrics']['by_source_file'].values()))['avg_latency_ms'],20)
  self.assertEqual(api.database_daily(c,NOW,lifetime=True)['database_metrics']['summary']['total_requests'],3)
 def test_existing_query_adapter_fields_order_and_explicit_hold(self):
  api=self.api();c,s=self.db([self.row(1,age=2),self.row(2,age=1)]);self.catchup(s)
  reader=api.CompactReadConnection(c,NOW)
  r=reader.cursor().execute("SELECT * FROM v_rpc_daily_savings WHERE day >= DATE('now', '-30 days') ORDER BY day ASC").fetchall()
  self.assertEqual([v['day'] for v in r],['2026-10-20','2026-10-21'])
  self.expect('UNSUPPORTED_DAILY_HORIZON',lambda:reader.execute("SELECT * FROM v_rpc_daily_savings WHERE day >= DATE('now', '-90 days')"))
 def test_reset_ties_bootstrap_late_updates_and_query_plans(self):
  c,s=self.db([self.row(i+1,age=1,credits=1) for i in range(300)]);self.catchup(s)
  s.reset('manual',NOW-2*86400,NOW-2*86400+124,10,NOW)
  self.expect('RESET_BACKFILL_INCOMPLETE',lambda:s.reset_report('manual',NOW,20))
  self.insert(c,self.row(301,age=.5,credits=2));c.commit();self.catchup(s)
  for _ in range(4):s.bootstrap_reset('manual')
  self.assertEqual(s.reset_report('manual',NOW,20)['local_credits_since_reset'],302)
  p=c.execute('EXPLAIN QUERY PLAN SELECT id FROM rpc_metrics WHERE timestamp=? AND id>? AND id<=? ORDER BY id LIMIT 128',(NOW-86400,0,300)).fetchall()
  self.assertIn('idx_rpc_metrics_timestamp',str(p));self.assertNotIn('TEMP B-TREE',str(p))
 def test_sync_existing_recorder_baseline_and_midnight_change(self):
  c,s=self.db([self.row(1,age=2,credits=9),self.row(2,age=.5,credits=7)])
  c.execute('CREATE TABLE rpc_metrics_state(key TEXT PRIMARY KEY,value TEXT)')
  vals={'comparison_reset_timestamp':str(NOW-86400),'comparison_reset_time':'2026-10-21T21:22:04+00:00','baseline_helius_credits_today':'100'}
  c.executemany('INSERT INTO rpc_metrics_state VALUES(?,?)',vals.items());m.sync_recorder_reset(s,NOW);self.catchup(s)
  self.assertEqual(s.reset_report('recorder_active',NOW,110)['local_credits_since_reset'],7)
  c.execute("UPDATE rpc_metrics_state SET value=? WHERE key='comparison_reset_timestamp'",(str(NOW),));m.sync_recorder_reset(s,NOW)
  self.assertEqual(s.reset_report('recorder_active',NOW,110)['local_credits_since_reset'],0)
  self.assertEqual(s.totals()['stats']['spent'],16)
 def test_recorder_cache_saved_includes_full_scan_and_custom_actions(self):
  c,s=self.db([self.row(1,cache_action='full_scan',credits_saved=8),self.row(2,cache_action='custom',credits_saved=9),self.row(3,cache_action='none',credits_saved=50)])
  s.seed_reset('x',NOW-1,NOW-1,0);self.catchup(s)
  self.assertEqual(s.reset_report('x',NOW,0)['saved_credits'],17)
 def test_default_slice_budget_and_existing_transaction_rollback(self):
  c,s=self.db([self.row(i+1) for i in range(200)],limits=m.Limits())
  first=s.advance(NOW);self.assertLessEqual(first['processed'],16);self.assertGreater(first['processed'],0)
  self.catchup(s);before=s.meta()['cursor']
  self.insert(c,self.row(201));s.advance(NOW);c.rollback()
  self.assertEqual(s.meta()['cursor'],before);self.assertEqual(s.totals()['stats']['n'],200)
 def test_disabled_hook_has_no_schema_or_io_side_effect(self):
  from unittest.mock import patch
  c=sqlite3.connect(':memory:')
  try:
   with patch.dict('os.environ',{'RPC_COMPACT_ENABLED':'0'}):self.assertIsNone(m.advance_if_enabled(c,NOW))
   self.assertEqual(c.execute('SELECT name FROM sqlite_master').fetchall(),[])
  finally:c.close()
 def test_time_budget_failure_and_expiry_are_restart_safe(self):
  c,s=self.db([self.row(1)],limits=m.Limits(seconds=.000000001))
  self.expect('SLICE_TIME_LIMIT',lambda:s.advance(NOW));self.assertEqual(s.meta()['cursor'],0)
 def test_overflow_unknown_action_preserves_cache_hits_and_savings(self):
  c,s=self.db([self.row(i+1,section='s'*100,cache_action='skip',credits_saved=5) for i in range(20)])
  self.catchup(s);self.assertEqual(s.totals()['stats']['hit_n'],20)
  r=s.report('v_rpc_cumulative_savings',NOW);self.assertTrue(r['dimensions_overflow']);self.assertEqual(r['data'][0]['total_credits_saved'],100)
 def test_clock_regression_and_counter_overflow_fail_closed(self):
  c,s=self.db([self.row(1,credits=2**63-1),self.row(2,credits=1)])
  self.expect('COUNTER_OVERFLOW',lambda:s.advance(NOW));self.assertEqual(s.meta()['cursor'],0)
  self.expect('CLOCK_REGRESSION',lambda:s.advance(NOW-86400))
 def test_flask_telemetry_routes_report_unsupported_and_overflow(self):
  try:from flask import Flask
  except ImportError:self.skipTest('run with algotrader Python for Flask fixture integration')
  from unittest.mock import patch
  import types
  api=self.api()
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'http.db');c,s=self.db([self.row(1,section='s'*100)],path);self.catchup(s);c.commit()
   app=Flask('dev003_fixture')
   def connect(*args,**kwargs):return sqlite3.connect(path)
   api.register_flask_contract(app,path,connect)
   @app.route('/api/rpc-savings/daily')
   def fixture_route():return {'existing_field':1}
   @app.route('/unrelated')
   def other_route():return {'ok':True}
   with patch.dict('os.environ',{'RPC_COMPACT_ENABLED':'1'}),patch.object(api,'time',types.SimpleNamespace(time=lambda:NOW)):
    client=app.test_client();r=client.get('/api/rpc-savings/daily?days=90');self.assertEqual(r.status_code,400);self.assertEqual(r.json['code'],'UNSUPPORTED_DAILY_HORIZON')
    r=client.get('/api/rpc-savings/daily?days=30');self.assertEqual(r.status_code,200);self.assertEqual(r.headers['X-RPC-Dimensions-Overflow'],'1')
    self.assertEqual(client.get('/unrelated').status_code,200)

 def test_atomic_install_and_idempotent_reinstall(self):
  c,s=self.db([self.row(1)]);before=[r[0] for r in c.execute('SELECT name FROM sqlite_master')]
  s.install(NOW);self.assertEqual(before,[r[0] for r in c.execute('SELECT name FROM sqlite_master')]);self.catchup(s)
  s.install(NOW);self.assertEqual(s.totals()['stats']['n'],1)
 def test_hook_failure_preserves_detailed_transaction(self):
  from unittest.mock import patch
  c,s=self.db();self.insert(c,self.row(1))
  with patch.dict('os.environ',{'RPC_COMPACT_ENABLED':'1'}),patch.object(m.CompactTelemetry,'advance',side_effect=RuntimeError('injected')):
   self.assertEqual(m.advance_if_enabled(c,NOW)['status'],'hold')
  c.commit();self.assertEqual(c.execute('SELECT COUNT(*) FROM rpc_metrics').fetchone()[0],1);self.assertEqual(s.meta()['cursor'],0)
 def test_one_flush_can_process_500_rows_without_double_count(self):
  from unittest.mock import patch
  c,s=self.db([self.row(i+1) for i in range(500)])
  with patch.dict('os.environ',{'RPC_COMPACT_ENABLED':'1'}):
   start=time.monotonic();result=m.advance_if_enabled(c,NOW)
   MEASUREMENTS['500_row_flush_seconds']=time.monotonic()-start
   MEASUREMENTS['500_row_flush_processed']=result['processed']
  self.assertEqual(result['processed'],500)
  self.catchup(s);self.assertEqual(s.totals()['stats']['n'],500)
 def test_real_cell_bound_and_storage_footprint(self):
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'bounded.db')
   c,s=self.db([self.row(i+1,section='s'+str(i)) for i in range(4200)],path)
   self.catchup(s);c.commit()
   self.assertEqual(s.totals()['stats']['n'],4200);self.assertTrue(s.totals()['dimensions_overflow'])
   self.assertLessEqual(c.execute("SELECT COUNT(*) FROM rpc_compact_cells WHERE scope='life' AND clock='timestamp'").fetchone()[0],4096)
   self.assertLessEqual(s.meta()['encoded_bytes'],m.MAX_ENCODED_BYTES)
   MEASUREMENTS['4200_dimension_fixture_db_bytes']=pathlib.Path(path).stat().st_size
   MEASUREMENTS['4200_dimension_fixture_encoded_bytes']=s.meta()['encoded_bytes']
   self.assertLess(pathlib.Path(path).stat().st_size,128*1024*1024)
 def test_wal_guard_pauses_summary_only(self):
  from unittest.mock import patch
  import types
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'fixture.db');c,s=self.db([self.row(1)],path)
   with patch('os.stat',return_value=types.SimpleNamespace(st_size=64*1024*1024)):
    self.expect('WAL_BUDGET_HOLD',lambda:s.advance(NOW))
   self.assertEqual(s.meta()['cursor'],0)
 def test_framework_endpoint_rejects_old_windows_without_app_startup(self):
  import ast,asyncio,os
  from unittest.mock import patch
  api=self.api();tree=ast.parse((ROOT/'src/apis/rpc_metrics_api.py').read_text())
  for n in tree.body:
   if isinstance(n,ast.AsyncFunctionDef) and n.name in ('metrics_database','metrics_rpc_optimizations','metrics_rpc_component_breakdown'):
    n.decorator_list=[];n.args.defaults=[];n.args.kw_defaults=[]
    ns={};exec(compile(ast.Module(body=[n],type_ignores=[]),'isolated-route','exec'),ns)
    with patch.dict(os.environ,{'RPC_COMPACT_ENABLED':'1'}):
     r=asyncio.run(ns[n.name](720));self.assertEqual(r['status'],'unsupported')
 def test_report_snapshot_excludes_concurrent_uncommitted_tail(self):
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'snapshot.db');c,s=self.db([self.row(1)],path);self.catchup(s);c.commit();c.execute('PRAGMA journal_mode=WAL')
   other=sqlite3.connect(path)
   try:
    self.insert(other,self.row(2))
    self.assertEqual(s.report('v_rpc_cumulative_savings',NOW)['data'][0]['total_requests'],1)
    other.commit();self.expect('BACKFILL_INCOMPLETE',lambda:s.report('v_rpc_cumulative_savings',NOW))
   finally:other.close()

 def test_existing_savings_efficiency_helpers_return_existing_dataclasses(self):
  import types,dataclasses
  from unittest.mock import patch
  api=self.api()
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'helpers.db');c,s=self.db([self.row(1,age=2),self.row(2,age=1,cache_action='skip',credits_saved=10)],path);self.catchup(s);c.commit()
   def connect(*args,**kwargs):return sqlite3.connect('file:'+path+'?mode=ro',uri=True)
   stub=types.ModuleType('src.utils.db_locking');stub.db_connect=connect
   with patch.dict(sys.modules,{'src.utils.db_locking':stub}),patch.dict('os.environ',{'RPC_COMPACT_ENABLED':'1'}),patch.object(api,'time',types.SimpleNamespace(time=lambda:NOW)):
    for filename,functions in [('rpc_savings_api',['query_daily_savings','query_section_breakdown','query_cache_performance','query_cumulative_summary']),('rpc_efficiency_api',['query_daily_efficiency','query_efficiency_all_time','query_efficiency_by_section','query_health_status'])]:
     name='isolated_'+filename;spec=importlib.util.spec_from_file_location(name,ROOT/'src/apis'/str(filename+'.py'));mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);mod.DB_PATH=path
     for name in functions:
      value=getattr(mod,name)();items=value if isinstance(value,list) else [value]
      self.assertTrue(items,name)
      for item in items:self.assertTrue(dataclasses.asdict(item),name)
 def test_changed_view_contract_is_hold_not_empty_metrics(self):
  c,s=self.db([self.row(1)]);self.catchup(s)
  c.execute('DROP VIEW v_rpc_daily_savings');c.execute('CREATE VIEW v_rpc_daily_savings AS SELECT 1 changed')
  self.expect('VIEW_CONTRACT_CHANGED',lambda:s.report('v_rpc_daily_savings',NOW))
 def test_process_exit_before_and_after_commit_replays_exactly_once(self):
  import subprocess
  with tempfile.TemporaryDirectory() as d:
   path=str(pathlib.Path(d)/'crash.db');c,s=self.db([self.row(1),self.row(2),self.row(3)],path)
   code="import importlib.util,sys,sqlite3,os; q=importlib.util.spec_from_file_location('compactchild',sys.argv[2]);m=importlib.util.module_from_spec(q);sys.modules[q.name]=m;q.loader.exec_module(m);c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE') if sys.argv[3]=='before' else None;m.CompactTelemetry(c,m.Limits(seconds=1)).advance("+str(NOW)+",limit=2);os._exit(17)"
   module=str(ROOT/'src/metrics/rpc_compact.py')
   for phase,expected in [('before',0),('after',2)]:
    result=subprocess.run([sys.executable,'-c',code,path,module,phase],timeout=5,capture_output=True)
    self.assertEqual(result.returncode,17,result.stderr);self.assertEqual(s.meta()['cursor'],expected)
   self.catchup(s);self.assertEqual(s.totals()['stats']['n'],3)

 def test_provider_method_http_status_dimensions_are_retained(self):
  c,s=self.db([self.row(1,provider='a',method='x',status_code=200),self.row(2,provider='a',method='x',status_code=201),self.row(3,provider='b',method='y',status_code=503)])
  self.catchup(s);retained={}
  for _,key,payload,_ in s._view_input('life','timestamp'):
   dims=dict(zip(m.DIMS,json.loads(key)));retained[(dims['provider'],dims['method'],dims['status_code'])]=json.loads(payload)['n']
  self.assertEqual(retained,{('a','x',200):1,('a','x',201):1,('b','y',503):1})

 def test_atomic_reset_generation_failure_cannot_publish_mixed_keys(self):
  c,s=self.db();c.execute('CREATE TABLE rpc_metrics_state(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
  old={'comparison_reset_timestamp':'1','comparison_reset_time':'old','baseline_helius_credits_today':'10'};m.write_recorder_state(c,old)
  c.execute("CREATE TRIGGER reject_generation BEFORE UPDATE ON rpc_metrics_state WHEN NEW.key='comparison_reset_time' BEGIN SELECT RAISE(ABORT,'injected generation failure'); END")
  with self.assertRaises(sqlite3.IntegrityError):m.write_recorder_state(c,{'comparison_reset_timestamp':'2','comparison_reset_time':'new','baseline_helius_credits_today':'20'})
  self.assertEqual(m.recorder_state_snapshot(c),old)
 def test_active_reset_generation_change_is_explicit_hold(self):
  c,s=self.db([self.row(1)]);c.execute('CREATE TABLE rpc_metrics_state(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
  values={'comparison_reset_timestamp':str(NOW-1),'comparison_reset_time':'2026-10-22T21:19:59+00:00','baseline_helius_credits_today':'10'};m.write_recorder_state(c,values);m.sync_recorder_reset(s,NOW);self.catchup(s)
  c.execute("UPDATE rpc_metrics_state SET value='20' WHERE key='baseline_helius_credits_today'")
  self.expect('RESET_GENERATION_NOT_CERTIFIED',lambda:s.reset_report('recorder_active',NOW,30))

 def test_detail_guard_runs_after_current_auto_reset_refresh(self):
  import ast,types,threading,typing
  from unittest.mock import patch
  self.api();tree=ast.parse((ROOT/'src/metrics/rpc_metrics_recorder.py').read_text())
  cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='RPCMetricsRecorder')
  class StopAfterGuard(Exception):pass
  for method in ('get_top_methods','get_optimization_savings','get_component_breakdown'):
   node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method)
   ns={'Dict':typing.Dict,'List':typing.List};exec(compile(ast.Module(body=[node],type_ignores=[]),'isolated-recorder-method','exec'),ns)
   obj=types.SimpleNamespace(_lock=threading.RLock(),_comparison_reset_timestamp=NOW-30*86400)
   obj._maybe_auto_reset_at_uk_midnight=lambda:setattr(obj,'_comparison_reset_timestamp',NOW)
   obj._refresh_reset_state_from_db=lambda:None
   def guard(hours,cutoff):
    self.assertEqual(cutoff,NOW);raise StopAfterGuard()
   with patch.object(m,'require_detail_window',guard),self.assertRaises(StopAfterGuard):ns[method](obj)
 def test_public_recorder_wrappers_preserve_delegation(self):
  import ast,typing,types
  tree=ast.parse((ROOT/'src/metrics/rpc_metrics_recorder.py').read_text())
  for method in ('get_optimization_savings','get_component_breakdown'):
   n=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==method)
   recorder=types.SimpleNamespace(**{method:lambda hours:{'hours':hours}})
   ns={'Dict':typing.Dict,'get_recorder':lambda:recorder};exec(compile(ast.Module(body=[n],type_ignores=[]),'isolated-public-wrapper','exec'),ns)
   self.assertEqual(ns[method](24),{'hours':24})

 def test_actual_bounded_reset_projection_is_index_ordered(self):
  c,s=self.db([self.row(1)])
  sql=f'SELECT {m.SOURCE_SELECT} FROM rpc_metrics WHERE timestamp>? AND timestamp<? ORDER BY rpc_metrics.timestamp DESC,rpc_metrics.id ASC LIMIT ?'
  plan=c.execute('EXPLAIN QUERY PLAN '+sql,(0,NOW+1,128)).fetchall()
  self.assertIn('idx_rpc_metrics_timestamp',str(plan));self.assertNotIn('TEMP B-TREE',str(plan))

if __name__=='__main__':unittest.main()
