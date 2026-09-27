import sqlite3
from src.ops.prospective_anchor_order_retention import *

def db():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row; ensure_schema(c); ensure_schema(c); return c
def anchor(): return {'operation_id':'op','route_type':'SELECTED_UPSTREAM','route_semantic_version':'v1','role_left':'pool','role_right':'coordinator','causal_direction':'POOL_TO_COORDINATOR','source_evidence_id':'route','ordering_evidence_id':'order','establishment_source':'independent_prospective_route'}
def candidate(): return {k:v for k,v in anchor().items() if k!='establishment_source'}
def test_anchor_is_idempotent_and_membership_independent():
    c=db(); one=persist_anchor(c,anchor(),established_at=1); two=persist_anchor(c,anchor(),established_at=2)
    assert one==two and c.execute('select count(*) from immutable_operation_causal_anchors').fetchone()[0]==1
    assert assess_anchor_continuity(c,candidate())=={'state':'QUALIFIED','anchor_id':one}
def test_anchor_missing_conflict_and_membership_source_fail_closed():
    c=db(); assert assess_anchor_continuity(c,candidate())['state']=='INSUFFICIENT_EVIDENCE'
    assert assess_anchor_continuity(c,{**candidate(),'conflict_state':True})['state']=='CONFLICT'
    try: persist_anchor(c,{**anchor(),'establishment_source':'membership'},established_at=1)
    except ValueError: pass
    else: assert False
def slot(c,s,slot): return persist_slot(c,{'signature':s,'slot':slot,'source_identity':'known','acquisition_identity':'one-request','semantic_version':'KNOWN_SIGNATURE_SLOT_V1'},acquired_at=1)
def ordinal(c,s,slot,n): return persist_ordinal(c,{'signature':s,'slot':slot,'transaction_ordinal':n,'block_evidence_id':'block','semantic_version':'TRANSACTION_ORDER_SLOT_ORDINAL_V1'},acquired_at=1)
def test_different_slot_and_reversed_order():
    c=db(); slot(c,'p',1); slot(c,'c',2); assert assess_order(c,'p','c')['state']=='PARENT_BEFORE_CHILD'
    c=db(); slot(c,'p',2); slot(c,'c',1); assert assess_order(c,'p','c')['state']=='PARENT_AFTER_CHILD'
def test_same_slot_ordinal_and_missing_fail_closed():
    c=db(); slot(c,'p',1); slot(c,'c',1); assert assess_order(c,'p','c')['state']=='INSUFFICIENT_EVIDENCE'
    ordinal(c,'p',1,1); ordinal(c,'c',1,2); assert assess_order(c,'p','c')['state']=='PARENT_BEFORE_CHILD'
    c=db(); slot(c,'p',1); slot(c,'c',1); ordinal(c,'p',1,2); ordinal(c,'c',1,1); assert assess_order(c,'p','c')['state']=='PARENT_AFTER_CHILD'
