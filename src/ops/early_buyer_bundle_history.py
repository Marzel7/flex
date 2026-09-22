"""Offline, bounded EARLY_BUYER_BUNDLE_HISTORY_V1 core (no provider I/O)."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
import base58
from src.utils.pubkey_validation import is_valid_pubkey

MAX_BUYS, MAX_SECONDS = 20, 60
@dataclass(frozen=True)
class NormalizedEarlyBuyerBundleV2:
    version: str='EARLY_BUYER_BUNDLE_NORMALIZATION_V2'
    normalized_rows: tuple=()
    identity_assertions: tuple=()
    resolved_identities: tuple=()
    identity_conflicts: tuple=()
    ordering: tuple=()
    completeness: tuple=()
    primary_reason_code: str|None=None
    secondary_reason_codes: tuple=()
    provenance: tuple=()
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()

REASON_CODES=('MINT_IDENTITY_UNRESOLVED','SIGNATURE_IDENTITY_UNRESOLVED','CREATOR_IDENTITY_UNRESOLVED','BUYER_IDENTITY_UNRESOLVED','IDENTITY_CONTRADICTION','ORDERING_UNRESOLVED','TRADE_SEQUENCE_INCOMPLETE','PROVIDER_COVERAGE_GAP','PROVIDER_RESPONSE_INCOMPLETE','ACQUISITION_BOUND_REACHED','OTHER_EARLY_BUYER_INSUFFICIENT')
PRECEDENCE=('IDENTITY_CONTRADICTION','MINT_IDENTITY_UNRESOLVED','CREATOR_IDENTITY_UNRESOLVED','BUYER_IDENTITY_UNRESOLVED','SIGNATURE_IDENTITY_UNRESOLVED','ORDERING_UNRESOLVED','PROVIDER_RESPONSE_INCOMPLETE','PROVIDER_COVERAGE_GAP','TRADE_SEQUENCE_INCOMPLETE','ACQUISITION_BOUND_REACHED','OTHER_EARLY_BUYER_INSUFFICIENT')
def reasons(codes):
    xs=sorted(set(codes),key=lambda x:PRECEDENCE.index(x) if x in PRECEDENCE else len(PRECEDENCE));return {'primary_reason_code':xs[0] if xs else None,'secondary_reason_codes':xs[1:]}
def assertion(field,value,source,evidence_id=None,event_id=None):
    valid=valid_signature(value) if field=='signature' else is_valid_pubkey(value)
    return {'assertion_id':digest([field,value,source,evidence_id,event_id]),'field':field,'event_id':event_id,'asserted_value':value,'normalized_value':value if valid else None,'source':source,'evidence_id':evidence_id,'validation_status':'VALID' if valid else ('MISSING' if value is None or value=='' else 'MALFORMED')}
def identity_resolution(field,assertions):
    a=sorted(assertions,key=lambda x:x['assertion_id']);good=[x for x in a if x['validation_status']=='VALID']; conflicts=[]
    if len({x['normalized_value'] for x in good})>1:
        for x in good[1:]:conflicts.append({'field':field,'left_assertion_id':good[0]['assertion_id'],'right_assertion_id':x['assertion_id'],'conflict_type':'VALID_VALUES_DIFFER','resolution':'UNRESOLVED','evidence_ids':sorted(filter(None,[good[0]['evidence_id'],x['evidence_id']]))})
    return {'value':good[0]['normalized_value'] if len(good)==1 else None,'assertions':a,'conflicts':conflicts}
def valid_signature(value):
    try: return isinstance(value,str) and len(base58.b58decode(value))==64
    except Exception: return False
def canonical_order_key(event):
    key=(event.get('slot'),event.get('transaction_index'),event.get('event_index'))
    return key if all(isinstance(v,int) for v in key) else None
def structural_ordering(trades):
    keys=[canonical_order_key(t) for t in trades]
    if any(k is None for k in keys): return ({'status':'ORDER_MISSING','resolution_status':'UNRESOLVED'},)
    if len(set(keys))!=len(keys): return ({'status':'ORDER_DUPLICATE','resolution_status':'UNRESOLVED','canonical_keys':tuple(sorted(keys))},)
    return ({'status':'ORDER_RESOLVED','resolution_status':'RESOLVED','canonical_keys':tuple(sorted(keys))},)
def completeness_state(trades):
    response=all(t.get('provider_response_complete',True) for t in trades)
    coverage=all(t.get('provider_window_complete',True) for t in trades)
    bound=any(t.get('acquisition_bound_reached',False) for t in trades) or len(trades)>=MAX_BUYS
    more=any(t.get('has_more',False) for t in trades)
    complete=response and coverage and (bound or not more)
    return {'sequence_complete':complete,'provider_response_complete':response,'provider_window_complete':coverage,'coverage_status':'COMPLETE' if coverage else 'GAP','acquisition_bound_reached':bound,'has_more':more}
def normalize(trades, creator, launch_time):
    if not is_valid_pubkey(creator): return [],'CREATOR_IDENTITY_UNRESOLVED'
    rows=[]
    # An unresolved structural key is evidence of insufficiency, never a licence
    # to manufacture an order from zero/defaults or a signature tiebreaker.
    source = trades if any(canonical_order_key(t) is None for t in trades) else sorted(trades,key=canonical_order_key)
    for t in source:
        if t.get('side','BUY')!='BUY' or t.get('buyer')==creator: continue
        if not is_valid_pubkey(t.get('buyer')): return [],'BUYER_IDENTITY_UNRESOLVED'
        if not valid_signature(t.get('signature')): return [],'SIGNATURE_IDENTITY_UNRESOLVED'
        if t.get('ordering_resolved') is False: return [],'ORDERING_UNRESOLVED'
        seconds=t.get('block_time',launch_time)-launch_time
        if seconds>MAX_SECONDS: break
        if len(rows)==MAX_BUYS: break
        r={k:t.get(k) for k in ('signature','slot','block_time','transaction_index','event_index','buyer','signers','fee_payer','sol_spent','token_amount','execution_price','venue','provider','provider_order')};r['ordinal']=len(rows)+1;r['seconds_from_launch']=seconds;r['seconds_from_previous_buy']=None if not rows else seconds-rows[-1]['seconds_from_launch'];rows.append(r)
    return rows,None
def normalize_v2(trades,creator,launch_time,mint=None,provider_mint=None,provider_creator=None):
    rows,legacy_reason=normalize(trades,creator,launch_time)
    assertions=[assertion('creator',creator,'FROZEN_CONTEXT')]
    if mint is not None: assertions.append(assertion('mint',mint,'FROZEN_CONTEXT'))
    if provider_mint is not None: assertions.append(assertion('mint',provider_mint,'PROVIDER'))
    if provider_creator is not None: assertions.append(assertion('creator',provider_creator,'PROVIDER'))
    for t in trades:
        event_id=digest(['EARLY_BUY_EVENT_V1',t.get('evidence_id'),t.get('signature'),t.get('buyer'),t.get('slot'),t.get('block_time')])
        assertions.append(assertion('buyer',t.get('buyer'),'PROVIDER',t.get('evidence_id'),event_id))
        if t.get('asserted_buyer') is not None: assertions.append(assertion('buyer',t['asserted_buyer'],'PROVIDER_ASSERTED',t.get('evidence_id'),event_id))
        assertions.append(assertion('signature',t.get('signature'),'PROVIDER',t.get('evidence_id'),event_id))
        if t.get('asserted_signature') is not None: assertions.append(assertion('signature',t['asserted_signature'],'PROVIDER_ASSERTED',t.get('evidence_id'),event_id))
    groups={}
    for a in assertions:
        key=(a['field'],a.get('event_id')) if a['field'] in ('buyer','signature') else (a['field'],None)
        groups.setdefault(key,[]).append(a)
    resolved=[(f if e is None else f+':'+e,identity_resolution(f,x)) for (f,e),x in sorted(groups.items()) if x]
    conflicts=tuple(c for _,x in resolved for c in x['conflicts'])
    ordering=structural_ordering(trades); completeness=completeness_state(trades);codes=[]
    if conflicts: codes.append('IDENTITY_CONTRADICTION')
    if legacy_reason: codes.append(legacy_reason)
    if ordering[0]['resolution_status']=='UNRESOLVED': codes.append('ORDERING_UNRESOLVED')
    if not completeness['provider_response_complete']: codes.append('PROVIDER_RESPONSE_INCOMPLETE')
    elif not completeness['provider_window_complete']: codes.append('PROVIDER_COVERAGE_GAP')
    elif not completeness['sequence_complete']: codes.append('TRADE_SEQUENCE_INCOMPLETE')
    rr=reasons(codes)
    ordered_rows = tuple(rows) if any(canonical_order_key(r) is None for r in rows) else tuple(sorted(rows,key=canonical_order_key))
    return NormalizedEarlyBuyerBundleV2(normalized_rows=ordered_rows,identity_assertions=tuple(sorted(assertions,key=lambda x:x['assertion_id'])),resolved_identities=tuple(resolved),identity_conflicts=conflicts,ordering=ordering,completeness=tuple(sorted(completeness.items())),primary_reason_code=rr['primary_reason_code'],secondary_reason_codes=tuple(rr['secondary_reason_codes']),provenance=(('legacy_reason',legacy_reason),))
def normalize_v1_compat(result):
    if not isinstance(result,NormalizedEarlyBuyerBundleV2): raise TypeError('V2_RESULT_REQUIRED')
    return list(result.normalized_rows),result.primary_reason_code

def classify(buys, relationships=()):
    candidates=[]
    for rel in relationships:
        members=sorted(set(rel.get('members',[])))
        ords=[b['ordinal'] for b in buys if b['buyer'] in members]
        if len(ords)<2: continue
        state='BUNDLE_QUALIFIED' if rel.get('qualifying_rule') else ('BUNDLE_REFUTED' if rel.get('contradicted') else ('BUNDLE_INSUFFICIENT_EVIDENCE' if rel.get('unresolved') else 'BUNDLE_CANDIDATE'))
        candidates.append({'candidate_id':digest([members,ords]),'member_addresses':members,'member_buy_ordinals':ords,'evidence_features':rel.get('features',[]),'classification':state,'confidence':'HIGH' if state=='BUNDLE_QUALIFIED' else 'INSUFFICIENT','reason_codes':rel.get('reason_codes',[]),'evidence_ids':rel.get('evidence_ids',[])})
    states={x['classification'] for x in candidates}; mint='BUNDLE_PRESENT' if 'BUNDLE_QUALIFIED' in states else ('BUNDLE_AMBIGUOUS' if 'BUNDLE_CANDIDATE' in states else ('BUNDLE_EVIDENCE_INSUFFICIENT' if 'BUNDLE_INSUFFICIENT_EVIDENCE' in states else 'NO_BUNDLE_OBSERVED'))
    return candidates,mint

def build(context,trades,relationships=()):
    buys,reason=normalize(trades,context['creator'],context['launch']['block_time']); status='EARLY_BUYER_INSUFFICIENT_EVIDENCE' if reason else 'EARLY_BUYER_HISTORY_QUALIFIED'; candidates,bundle=classify(buys,relationships)
    buyers=[]
    for a in sorted({x['buyer'] for x in buys}):
        xs=[x for x in buys if x['buyer']==a];buyers.append({'address':a,'first_buy_ordinal':xs[0]['ordinal'],'first_buy_signature':xs[0]['signature'],'first_buy_time':xs[0]['block_time'],'buy_count_in_window':len(xs),'total_sol_in_window':sum(x.get('sol_spent') or 0 for x in xs),'total_tokens_in_window':sum(x.get('token_amount') or 0 for x in xs),'creator_related':False,'provider_tags':[],'evidence_ids':[]})
    r={**context,'history_stage':'EARLY_BUYER_BUNDLE_HISTORY','history_stage_version':'V1','first_actionable_buy':None,'window':{'max_non_creator_buys':20,'max_seconds':60,'start_time':context['launch']['block_time'],'observed_buy_count':len(buys),'unique_buyer_count':len(buyers),'complete':not reason,'terminal_reason':reason},'early_buys':buys,'early_buyers':buyers,'bundle_candidates':candidates,'bundle_summary':{'status':bundle,'qualified_bundle_count':sum(x['classification']=='BUNDLE_QUALIFIED' for x in candidates),'candidate_bundle_count':len(candidates)},'qualification':{'early_buyer_history_status':status,'bundle_history_status':bundle,'reason_codes':[] if not reason else [reason]}}
    r['logical_id']=digest(r);return r
def build_v2(context,normalized,relationships=()):
    if not isinstance(normalized,NormalizedEarlyBuyerBundleV2): raise TypeError('V2_NORMALIZATION_REQUIRED')
    buys=[dict(x,ordinal=i+1) for i,x in enumerate(normalized.normalized_rows)]
    candidates,bundle=classify(buys,relationships)
    r={**context,'history_stage':'EARLY_BUYER_BUNDLE_HISTORY','history_stage_version':'V2','first_actionable_buy':None,'early_buys':buys,'identity_assertions':normalized.identity_assertions,'resolved_identities':normalized.resolved_identities,'identity_conflicts':normalized.identity_conflicts,'ordering':normalized.ordering,'completeness':normalized.completeness,'qualification':{'primary_reason_code':normalized.primary_reason_code,'secondary_reason_codes':normalized.secondary_reason_codes},'bundle_candidates':candidates,'bundle_summary':{'status':bundle}}
    r['logical_id']=digest(r);return r

LIMITS={'birdeye':2,'helius_transaction':10,'helius_block':2,'relationship_lookup':5}
PILOT_NETWORK_MAX=150*1024*1024; PILOT_DURABLE_MAX=5*1024*1024; RECORD_MAX=512*1024
def ceiling_ok(value,limit): return isinstance(value,int) and 0<=value<=limit
def plan_escalation(candidate_id,mint,current,missing_fact,call,can_change,calls_used,objective='BUNDLE_RELATIONSHIP',estimated_blocks=0,block_ceiling=2):
    if call not in LIMITS: raise ValueError('UNSAFE_ESCALATION_PATH')
    used=calls_used.get(call,0); remaining=LIMITS[call]-used
    allowed_objective=objective in ('BUNDLE_RELATIONSHIP','EARLY_HISTORY_ORDERING')
    recoverable=not (objective=='EARLY_HISTORY_ORDERING' and estimated_blocks>block_ceiling)
    decision='AUTHORIZED' if can_change and remaining>0 and allowed_objective and recoverable else ('BUDGET_EXHAUSTED' if remaining<=0 else 'SEMANTICALLY_IRRELEVANT')
    return {'candidate_id':candidate_id,'mint':mint,'current_classification':current,'missing_fact':missing_fact,'provider_call_requested':call,'objective':objective,'how_result_can_change_classification':can_change,'estimated_blocks':estimated_blocks,'block_ceiling':block_ceiling,'calls_used':used,'calls_remaining':max(0,remaining),'decision':decision,'reason_code':decision}
def checkpoint_counters(counters):
    if set(counters)-set(LIMITS): raise ValueError('UNSAFE_COUNTER_CATEGORY')
    return tuple(sorted((k,int(v)) for k,v in counters.items()))
def restore_counters(checkpoint): return dict(checkpoint)

class OfflineStore:
    def __init__(self,path): self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
    def commit(self,record):
        p=self.path/(record['mint']+'.json'); raw=json.dumps(record,sort_keys=True,separators=(',',':'))
        if len(raw.encode())>RECORD_MAX: raise RuntimeError('RECORD_SIZE_LIMIT_EXCEEDED')
        if p.exists() and p.read_text()!=raw: raise RuntimeError('TERMINAL_RECORD_CONFLICT')
        tmp=p.with_suffix('.tmp');tmp.write_text(raw);tmp.replace(p)
        if p.read_text()!=raw: raise RuntimeError('READBACK_FAILED')
        return digest(record)
    def read(self,mint): return json.loads((self.path/(mint+'.json')).read_text())

HOOKS=('AFTER_INPUT_FROZEN','AFTER_PAYLOAD_RECEIVED','AFTER_PAYLOAD_HASHED','AFTER_NORMALIZATION','AFTER_CLASSIFICATION','AFTER_COMPACT_WRITE','AFTER_COMMIT','AFTER_READBACK_VERIFICATION','BEFORE_DELETE','AFTER_DELETE','BEFORE_TERMINAL')
def offline_execute(context,trades,store,relationships=(),crash_at=None,checkpoint=None):
    """Test-only deterministic lifecycle; source payload exists only in this call."""
    cp=dict(checkpoint or {'state':'NOT_STARTED','counters':{}})
    def step(state):
        cp['state']=state
        if crash_at==state: raise RuntimeError('OFFLINE_INJECTED_'+state)
    step('AFTER_INPUT_FROZEN'); cp['input_digest']=digest(context)
    step('AFTER_PAYLOAD_RECEIVED'); cp['response_digest']=digest(trades)
    step('AFTER_PAYLOAD_HASHED'); record=build(context,trades,relationships)
    step('AFTER_NORMALIZATION'); cp['logical_id']=record['logical_id']
    step('AFTER_CLASSIFICATION'); step('AFTER_COMPACT_WRITE')
    store.commit(record); step('AFTER_COMMIT'); store.read(context['mint'])
    step('AFTER_READBACK_VERIFICATION'); step('BEFORE_DELETE'); cp['source_deleted']=True
    step('AFTER_DELETE'); step('BEFORE_TERMINAL'); cp['state']='TERMINAL'; return record,cp
def offline_execute_v2(context,normalized,store,relationships=(),crash_at=None,checkpoint=None):
    cp=dict(checkpoint or {'state':'NOT_STARTED','counters':{}})
    def step(state):
        cp['state']=state
        if crash_at==state: raise RuntimeError('OFFLINE_INJECTED_'+state)
    step('AFTER_INPUT_FROZEN'); record=build_v2(context,normalized,relationships);cp['logical_id']=record['logical_id']
    step('AFTER_COMPACT_WRITE'); store.commit(record);step('AFTER_COMMIT')
    persisted=store.read(context['mint'])
    if digest(persisted)!=digest(json.loads(json.dumps(record))): raise RuntimeError('READBACK_FAILED')
    step('AFTER_READBACK_VERIFICATION');step('BEFORE_DELETE');cp['source_deleted']=True;step('AFTER_DELETE');step('BEFORE_TERMINAL');cp['state']='TERMINAL';return record,cp

def repeated_buyer_index(records):
    out=defaultdict(lambda:{'mints':[],'opening_positions':[],'first_seen':None,'last_seen':None,'qualified_bundle_count':0,'candidate_bundle_count':0,'creator_relationship_count':0})
    for r in records:
        for b in r['early_buys']:
            x=out[b['buyer']];x['mints'].append(r['mint']);x['opening_positions'].append(b['ordinal']);x['first_seen']=min(filter(lambda v:v is not None,[x['first_seen'],b['block_time']]),default=b['block_time']);x['last_seen']=max(filter(lambda v:v is not None,[x['last_seen'],b['block_time']]),default=b['block_time'])
    return [{'buyer_address':k,'mint_count':len(set(v['mints'])),'mint_identities':sorted(set(v['mints'])),**v} for k,v in sorted(out.items())]

def cleanup_state(state,verified=False):
    allowed={'TRANSIENT':'RETAIN_UNTIL_COMMIT','RETAIN_UNTIL_COMMIT':'DELETE_ELIGIBLE' if verified else None,'DELETE_ELIGIBLE':'DELETED'}
    nxt=allowed.get(state)
    if not nxt: raise RuntimeError('CLEANUP_BEFORE_VERIFICATION')
    return nxt
