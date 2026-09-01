"""Address-blind, read-only semantic detector for direct creator provisioning."""
from __future__ import annotations
from typing import Any

DETECTOR_ID = "DIRECT_10K_CREATOR_PROVISIONING"
UNIQUE_MATCH, NO_MATCH, INSUFFICIENT_INPUT, AMBIGUOUS = "UNIQUE_MATCH", "NO_MATCH", "INSUFFICIENT_INPUT", "AMBIGUOUS"

def detect_direct_10k_creator_provisioning(evidence: dict[str, Any]) -> dict[str, str]:
    """Evaluate retained Walkback/transaction evidence; never writes or acquires."""
    required=("mint","creator","direct_funder","defining_signature","transfer_source","transfer_destination","transfer_amount_lamports","launch_coupled")
    missing=[k for k in required if evidence.get(k) in (None,"")]
    if missing: return {"result":INSUFFICIENT_INPUT,"reason":"INSUFFICIENT_"+missing[0].upper()}
    if evidence.get("ambiguous_transfer"): return {"result":AMBIGUOUS,"reason":"AMBIGUOUS_TRANSFER"}
    if evidence.get("intermediary_route"): return {"result":NO_MATCH,"reason":"NO_MATCH_INTERMEDIARY_ROUTE"}
    if evidence["transfer_amount_lamports"] != 10_000: return {"result":NO_MATCH,"reason":"NO_MATCH_AMOUNT"}
    if evidence["transfer_source"] != evidence["direct_funder"]: return {"result":NO_MATCH,"reason":"NO_MATCH_SOURCE_ROLE"}
    if evidence["transfer_destination"] != evidence["creator"]: return {"result":NO_MATCH,"reason":"NO_MATCH_DESTINATION_ROLE"}
    if not evidence["launch_coupled"]: return {"result":NO_MATCH,"reason":"NO_MATCH_TRANSACTION_ROLE"}
    signers=evidence.get("signers")
    fee=evidence.get("fee_payer")
    if signers is not None and (len(signers)!=1 or evidence["direct_funder"] not in signers): return {"result":NO_MATCH,"reason":"NO_MATCH_INTERMEDIARY_ROUTE"}
    if fee is not None and fee != evidence["direct_funder"]: return {"result":NO_MATCH,"reason":"NO_MATCH_TRANSACTION_ROLE"}
    return {"result":UNIQUE_MATCH,"reason":"MATCH_DIRECT_10K_TO_ASSOCIATED_CREATOR"}
