"""Fail-closed normalization of explicit and compiled Solana outer instructions."""
from __future__ import annotations

class InstructionNormalizationError(ValueError): pass

def _key(value): return value if isinstance(value,str) else value.get("pubkey")
def canonical_account_keys(transaction):
    try:
        message=transaction["transaction"]["message"];meta=transaction["meta"]
        static=[_key(x) for x in message["accountKeys"]]
        if not all(isinstance(x,str) and x for x in static): raise InstructionNormalizationError("STATIC_ACCOUNT_KEY_INVALID")
        loaded=meta.get("loadedAddresses") or {}
        writable=loaded.get("writable") or [];readonly=loaded.get("readonly") or []
        if not all(isinstance(x,str) and x for x in writable+readonly): raise InstructionNormalizationError("LOADED_ACCOUNT_KEY_INVALID")
        return static+writable+readonly
    except (KeyError,TypeError): raise InstructionNormalizationError("ACCOUNT_KEY_VECTOR_UNAVAILABLE")

def normalize_outer_instructions(transaction):
    keys=canonical_account_keys(transaction);raw=transaction["transaction"]["message"].get("instructions")
    if not isinstance(raw,list): raise InstructionNormalizationError("OUTER_INSTRUCTIONS_UNAVAILABLE")
    output=[]
    for outer_index,instruction in enumerate(raw):
        if "programId" in instruction:
            program_id=instruction["programId"];accounts=list(instruction.get("accounts") or [])
            if not isinstance(program_id,str) or not all(isinstance(x,str) for x in accounts): raise InstructionNormalizationError("EXPLICIT_INSTRUCTION_INVALID")
            shape="PARSED_EXPLICIT"
        elif "programIdIndex" in instruction:
            index=instruction["programIdIndex"];indices=instruction.get("accounts") or []
            if not isinstance(index,int) or index<0 or index>=len(keys): raise InstructionNormalizationError("PROGRAM_ID_INDEX_OUT_OF_RANGE")
            if not all(isinstance(x,int) and 0<=x<len(keys) for x in indices): raise InstructionNormalizationError("ACCOUNT_INDEX_OUT_OF_RANGE")
            program_id=keys[index];accounts=[keys[x] for x in indices];shape="COMPILED"
        else: raise InstructionNormalizationError("INSTRUCTION_PROGRAM_UNAVAILABLE")
        output.append({"outer_index":outer_index,"program_id":program_id,"resolved_accounts":accounts,"instruction_data":instruction.get("data"),"source_shape":shape})
    return output
