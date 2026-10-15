"""Regression: selected terminal facts outside priority LIMIT remain visible."""
import sqlite3
import pytest
from tests.test_dev037_rich_source_composition import read_only_guards, MONITOR

PAIRS = {
 'DNtZMNDZ65hAJbJXRiP9NnV98ZTrwLTEJZ78fWKCpump':(144175.59776543334,594764.794662167),
 '6LdjC13zbAxmruKrqsAQ4znUrjNe7gXXVeCHDBMYpump':(162775.9385584885,274476.6056229458),
 '6KuKphbVWZoz4kagf5QGaJEsUxJBY99LdBetfvwtpump':(125378.29249620523,125378.29249620523),
 '7JVDPbS8iYHoUD4yWYxQH9yHcop5oPeBnfBvXkiqpump':(128917.09988926353,168300.03364945002),
 '9JtPfLbN32CszHmstoXYdWaazFLaoxhfqf9mKgQMpump':(105600.45900012992,222174.89682065282),
}
MISSING=['E8bCunY5FYCtpmCeL1oCWgKLeFxA9z4oMNBe9MLVpump',
         '2fDAjAjyvCFATm5ERrJnyqqcqiZ9akV9vEFZLLGfpump',
         'EHFJLG7CbNnnuPHXHNtdeWfRQ3UFwBfBZRe11hnppump']

def test_exact_retained_pairs_outside_priority_subset(monkeypatch):
    from src.ops import operator_routes as routes
    monkeypatch.setenv('MISSION_CONTROL_MONITOR_UI_DB_PATH',str(MONITOR))
    identities=[('watchtower',mint) for mint in [*PAIRS,*MISSING]]
    facts=routes._selected_monitor_facts(identities)
    assert set(facts)=={('watchtower',mint) for mint in PAIRS}
    projected=routes._recent_operation_tokens(list(facts.values()),now=1792100000)
    for row in projected:
        opening,peak=PAIRS[row['mint']]
        fact=facts[('watchtower',row['mint'])]
        assert row['entry_mc_usd']==opening and row['peak_mc_usd']==peak
        assert row['peak_scope']=='RETAINED PEAK'
        assert row['max_multiple']==fact['running_peak_multiple']
        assert fact['entry_status']=='QUALIFIED'
        assert fact['monitor_state']=='PRICE_MONITOR_COMPLETE_COLLAPSED'
        assert fact['final_proven_ath_mc'] is None
    connection=sqlite3.connect(MONITOR)
    try:
        subset={r[0] for r in connection.execute(
            "SELECT mint FROM operation_monitor_facts WHERE operation_id IN ('watchtower','byzantine') "
            "ORDER BY CASE WHEN monitor_state LIKE 'MONITORING%' THEN 0 WHEN monitor_state LIKE 'WAITING%' THEN 1 ELSE 2 END,"
            "next_observation_at ASC,assignment_timestamp DESC LIMIT 20")}
        assert not (set(PAIRS)&subset)
        plan=[r[3] for r in connection.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM operation_monitor_facts WHERE operation_id=? AND mint=?',identities[0])]
        assert any('SEARCH' in line and 'INDEX' in line and 'mint=?' in line for line in plan)
        assert not any('SCAN' in line for line in plan)
    finally:
        connection.close()

def test_budget_no_unrelated_identity_lookup(monkeypatch):
    from src.ops import operator_routes as routes
    calls=[]
    class Connection:
        row_factory=None
        def execute(self,sql,params):
            calls.append((sql,params));return self
        def fetchone(self):return None
        def close(self):calls.append(('CLOSE',()))
    monkeypatch.setattr(routes,'_mission_control_monitor_read_connection',lambda:Connection())
    assert routes._selected_monitor_facts([('watchtower','chosen')]*3)=={}
    assert calls==[('SELECT * FROM operation_monitor_facts WHERE operation_id=? AND mint=?',('watchtower','chosen')),('CLOSE',())]
    with pytest.raises(routes.MissionControlMonitorAuthorityError,match='budget'):
        routes._selected_monitor_facts([('watchtower',str(i)) for i in range(21)])
