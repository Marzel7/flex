"""Compact diagnostic witness for the immutable first-buy anchor binding rule."""
from __future__ import annotations
import hashlib,json
from .opening_price_first_buy_amounts import PUMP_FUN
from .opening_price_instruction_normalization import InstructionNormalizationError, normalize_outer_instructions

VERSION="OPENING_PRICE_FIRST_BUY_BINDING_WITNESS_V1"
def canonical(value): return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def pubkey(item): return item if isinstance(item,str) else item.get("pubkey")

def build_anchor_binding_witness(anchor, transaction, source_response_digest=None):
    """Explain the current rule without altering it or retaining transaction payloads."""
    try:
        message=transaction["transaction"]["message"];meta=transaction["meta"]
        keys=message["accountKeys"]; signature=transaction["transaction"]["signatures"][0];slot=transaction["slot"]
        fee_payer=pubkey(keys[0]);signers=sorted(pubkey(x) for x in keys if isinstance(x,dict) and x.get("signer"))
        candidates=[]
        for instruction in normalize_outer_instructions(transaction):
            if instruction["program_id"]!=PUMP_FUN: continue
            accounts=instruction["resolved_accounts"];shape={"account_count":len(accounts),"has_data":bool(instruction.get("instruction_data")),"parsed_type":None}
            candidates.append({"outer_index":instruction["outer_index"],"source_shape":instruction["source_shape"],"program_id":PUMP_FUN,"resolved_accounts":accounts,"resolved_accounts_count":len(accounts),"contains_expected_mint":anchor.get("mint") in accounts,"contains_expected_buyer":anchor.get("buyer") in accounts,"instruction_shape":shape})
        logs=meta.get("logMessages") or [];buy_count=sum("Instruction: Buy" in x for x in logs)
        selected=next((x for x in candidates if x["contains_expected_mint"] and x["contains_expected_buyer"]),None)
        signature_match=signature==anchor.get("signature");slot_match=slot==anchor.get("slot")
        if not signature_match: reason="SIGNATURE_MISMATCH"
        elif not slot_match: reason="SLOT_MISMATCH"
        elif not candidates: reason="NO_PUMPFUN_INSTRUCTION"
        elif not any(x["contains_expected_mint"] for x in candidates): reason="EXPECTED_MINT_NOT_IN_CANDIDATE"
        elif not any(x["contains_expected_buyer"] for x in candidates): reason="EXPECTED_BUYER_NOT_IN_CANDIDATE"
        elif not selected: reason="MINT_AND_BUYER_SPLIT_ACROSS_INSTRUCTIONS"
        elif not buy_count: reason="BUY_LOG_MISSING"
        else: reason="BOUND"
        witness={"anchor_id":anchor.get("anchor_id"),"expected_signature":anchor.get("signature"),"expected_slot":anchor.get("slot"),"expected_mint":anchor.get("mint"),"expected_buyer":anchor.get("buyer"),"expected_transaction_index":anchor.get("transaction_index"),"expected_event_index":anchor.get("event_index"),"observed_signature":signature,"observed_slot":slot,"observed_fee_payer":fee_payer,"observed_signers":signers,"expected_buyer_is_fee_payer":anchor.get("buyer")==fee_payer,"expected_buyer_is_signer":anchor.get("buyer") in signers,"pumpfun_outer_instruction_count":len(candidates),"pumpfun_outer_instructions":candidates,"buy_log_present":bool(buy_count),"pumpfun_buy_log_count":buy_count,"matching_log_fragments_digest":digest([x for x in logs if "Instruction: Buy" in x]),"signature_match":signature_match,"slot_match":slot_match,"candidate_event_count":len(candidates),"selected_event":selected,"mint_match":bool(selected),"buyer_match":bool(selected),"buy_log_match":bool(buy_count),"binding_result":"BOUND" if reason=="BOUND" else "UNBOUND","binding_failure_reason":reason,"source_response_digest":source_response_digest or digest(transaction),"witness_version":VERSION}
        witness["witness_digest"]=digest(witness);return witness
    except (KeyError,IndexError,TypeError,InstructionNormalizationError):
        witness={"anchor_id":anchor.get("anchor_id"),"binding_result":"UNBOUND","binding_failure_reason":"PUMPFUN_EVENT_SHAPE_UNSUPPORTED","source_response_digest":source_response_digest or digest(transaction),"witness_version":VERSION};witness["witness_digest"]=digest(witness);return witness
