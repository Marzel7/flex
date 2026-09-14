"""Append-only, non-authoritative V1 promotion shadow store."""
import json
from hashlib import sha256
from src.ops.operation_attribution_evidence import build_decision

PROMOTION_SHADOW_CONTRACT_VERSION="OPERATION_ATTRIBUTION_PROMOTION_SHADOW_V1"

def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS operation_attribution_shadow_decisions (decision_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,decision_json TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(candidate_id,decision_id))")

def shadow_decide(conn, *, candidate_id, proposed_operation_id, detector_contract, families, production_decision_observed, grandfathering_state="NOT_GRANDFATHERED", **kwargs):
    """Write one compact decision in its own short transaction; never membership."""
    d=build_decision(candidate_id,proposed_operation_id,detector_contract,families,**kwargs)
    shadow="SHADOW_GRANDFATHERED" if grandfathering_state=="GRANDFATHERED" else "SHADOW_MATCH" if bool(production_decision_observed)==d["promotion_eligible"] else "SHADOW_WOULD_BLOCK" if production_decision_observed else "SHADOW_WOULD_PROMOTE"
    d.update({"promotion_shadow_contract_version":PROMOTION_SHADOW_CONTRACT_VERSION,"production_decision_observed":bool(production_decision_observed),"shadow_decision":d["attribution_state"],"decision_agreement_state":shadow,"grandfathering_state":grandfathering_state,"would_block_if_enforced":bool(production_decision_observed) and not d["promotion_eligible"],"would_promote_if_enforced":not bool(production_decision_observed) and d["promotion_eligible"]})
    key=sha256(json.dumps(d,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    ensure_schema(conn); conn.execute("BEGIN IMMEDIATE"); conn.execute("INSERT OR IGNORE INTO operation_attribution_shadow_decisions VALUES(?,?,?,0)",(key,candidate_id,json.dumps(d,sort_keys=True))); conn.commit(); return d,key
