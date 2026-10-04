import sqlite3
from src.ops.canonical_membership_outbox import ensure_schema,append_transition
from src.ops.universal_assignment_bridge_consumer import Authorities,Consumer,canonical_outbox_reader,TARGETED_PEPEINU
from src.ops.universal_outbox_runtime_composition import durable_opening_then_handoff
from src.ops.wsol_10_sol_four_step_operation import OPERATOR_ID as BYZANTINE
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID as WATCHTOWER

AUTH=Authorities('canonical','universal','canonical','universal')
def opening(adapter,event):
    return {'qualified':True,'timestamp':1791112830,'mc_usd':3925.9516827750313,'provenance':adapter}
def setup():
    canonical=sqlite3.connect(':memory:');ensure_schema(canonical)
    consumer=Consumer(sqlite3.connect(':memory:'),AUTH,'one',0,'head','head')
    opening_db=sqlite3.connect(':memory:');handoffs=[]
    return canonical,consumer,opening_db,handoffs
def add(c,op,mint='m',event='e',at=1):
    return append_transition(c,event_type='MEMBERSHIP_ASSIGNED',mint=mint,operator_id=op,canonical_event_id=event,assigned_at=at,previous_operator_id=None,writer_identity='fixture',created_at=at)
def test_real_outbox_reader_registry_opening_persistence_and_handoff():
    canonical,consumer,odb,handoffs=setup(); add(canonical,'watchtower','w','w',1);add(canonical,'byzantine','b','b',2)
    composed=durable_opening_then_handoff(odb,lambda assignment,entry,hid: handoffs.append((assignment,entry,hid)))
    assert consumer.run_once(canonical_outbox_reader(canonical,source_authority='canonical'),opening,composed,3)==['UNIVERSAL_HANDED_OFF','UNIVERSAL_HANDED_OFF']
    assert consumer.cursor()==2 and len(handoffs)==2
    assert odb.execute("select count(*) from universal_opening_work where state='QUALIFIED'").fetchone()[0]==2
def test_canonical_operator_ids_route_to_existing_registry():
    canonical,consumer,odb,handoffs=setup(); add(canonical,WATCHTOWER,'w','w',1);add(canonical,BYZANTINE,'b','b',2)
    assert consumer.run_once(canonical_outbox_reader(canonical,source_authority='canonical'),opening,durable_opening_then_handoff(odb,lambda *x:handoffs.append(x)),3)==['UNIVERSAL_HANDED_OFF','UNIVERSAL_HANDED_OFF']
def test_nexus_future_are_durable_adapter_unavailable_without_handoff():
    canonical,consumer,odb,handoffs=setup();add(canonical,'nexus','n','n',1);add(canonical,'future','f','f',2)
    assert consumer.run_once(canonical_outbox_reader(canonical,source_authority='canonical'),opening,durable_opening_then_handoff(odb,lambda *x:handoffs.append(x)),3)==['ADAPTER_UNAVAILABLE','ADAPTER_UNAVAILABLE']
    assert consumer.cursor()==2 and not handoffs
def test_crash_before_cursor_replays_without_duplicate_opening_or_handoff():
    canonical,consumer,odb,handoffs=setup();add(canonical,'byzantine','p',TARGETED_PEPEINU,1)
    composed=durable_opening_then_handoff(odb,lambda assignment,entry,hid: handoffs.append(hid))
    event=canonical_outbox_reader(canonical,source_authority='canonical')(0)[0]
    try: consumer.process(event,opening,composed,2,fault='after_handoff')
    except RuntimeError: pass
    # Consumer's projection is durable; replay does not call adapter/handoff again.
    assert consumer.process(event,lambda *_:(_ for _ in ()).throw(AssertionError()),composed,3)=='UNIVERSAL_HANDED_OFF'
    assert len(handoffs)==1 and consumer.cursor()==1
def test_targeted_pepeinu_does_not_move_forward_cursor():
    canonical,consumer,odb,handoffs=setup();add(canonical,'byzantine','p',TARGETED_PEPEINU,9)
    event=canonical_outbox_reader(canonical,source_authority='canonical')(0)[0]
    assert consumer.reconcile_pepeinu(event,opening,durable_opening_then_handoff(odb,lambda *x:handoffs.append(x)),2)=='UNIVERSAL_HANDED_OFF'
    assert consumer.cursor()==0 and len(handoffs)==1
