"""Injectable domain adapter for durable Pump.fun opening-history execution.

Provider I/O stays outside this module: ``transport`` receives an RPC method
and params and returns the transient response bytes.  This adapter immediately
normalizes the response and returns compact facts only.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable, Mapping

from .opening_price_semantic_reacquisition import VERSION as REDUCER_VERSION, reduce_block, price_and_valuation

VERSION = "OPENING_HISTORY_EXECUTION_ADAPTER_V1"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
BLOCK_PARAMS = {"transactionDetails":"full","encoding":"jsonParsed","rewards":False,"maxSupportedTransactionVersion":0}

def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

class AdapterError(RuntimeError): pass

class OpeningHistoryExecutionAdapter:
    def __init__(self, transport: Callable[[str, list], bytes], profile: Mapping[str, object]):
        self.transport, self.profile = transport, dict(profile)

    @staticmethod
    def create_request(signature: str) -> tuple[str, list]:
        return "getTransaction", [signature, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]

    def build_create_request(self,row): return self.create_request(row['create_signature'])

    @staticmethod
    def block_request(slot: int) -> tuple[str, list]:
        return "getBlock", [slot, BLOCK_PARAMS]

    def build_block_request(self,row,slot,phase):
        if phase not in {'CREATE_SLOT','EXTENSION_1','EXTENSION_2'}: raise AdapterError('INVALID_BLOCK_PHASE')
        return self.block_request(slot)

    def invoke(self, method: str, params: list) -> tuple[bytes, dict]:
        raw = self.transport(method, params)
        if not isinstance(raw, bytes): raise AdapterError("MALFORMED_RESPONSE")
        digest = sha256(raw).hexdigest()
        try: payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc: raise AdapterError("MALFORMED_RESPONSE") from exc
        if isinstance(payload, Mapping) and payload.get("error") is not None: raise AdapterError("HTTP_NONRETRYABLE")
        return raw, {"wire_bytes":len(raw), "response_digest":digest, "payload":payload}

    def qualify_create(self, row: Mapping[str, object], payload: Mapping[str, object]) -> dict:
        tx = payload.get("result", payload)
        if not isinstance(tx, Mapping) or (tx.get("meta") or {}).get("err") is not None: raise AdapterError("IDENTITY_MISMATCH")
        msg=(tx.get("transaction") or {}).get("message") or {}; keys=[x.get("pubkey") if isinstance(x,Mapping) else x for x in msg.get("accountKeys") or []]
        signature=((tx.get("transaction") or {}).get("signatures") or [None])[0]
        programs=[x.get("programId") for x in msg.get("instructions") or [] if isinstance(x,Mapping)]
        logs=(tx.get("meta") or {}).get("logMessages") or []
        creator=keys[0] if keys else None
        if signature!=row.get("create_signature") or row.get("mint") not in keys or PUMP not in programs or not any("Instruction: Create" in x for x in logs if isinstance(x,str)) or creator!=row.get("creator") or not isinstance(tx.get("slot"),int): raise AdapterError("IDENTITY_MISMATCH")
        return {"signature":signature,"slot":tx["slot"],"time":tx.get("blockTime"),"creator":creator,"create_log_indices":[i for i,x in enumerate(logs) if x=="Program log: Instruction: Create"]}

    def normalize_create_response(self,row,payload): return self.qualify_create(row,payload)
    def continue_from_create_handoff(self,row,handoff):
        if not isinstance(handoff,Mapping) or handoff.get('signature')!=row.get('create_signature') or handoff.get('creator')!=row.get('creator') or not isinstance(handoff.get('slot'),int): raise AdapterError('CREATE_HANDOFF_INVALID')
        return dict(handoff)
    def normalize_block_response(self,row,slot,payload,response_bytes):
        return reduce_block(payload,slot=slot,mint=row['mint'],creator=row['creator'],response_bytes=response_bytes)
    def reduce_opening_from_handoffs(self,row,create_handoff,block_handoffs):
        if not block_handoffs: raise AdapterError('BLOCK_HANDOFF_INVALID')
        return self.reduce(row,self.continue_from_create_handoff(row,create_handoff),sorted(block_handoffs,key=lambda x:x['slot']))
    def build_reserved_extension_request(self,row,reservation):
        if not isinstance(reservation,Mapping) or not isinstance(reservation.get('reserved_slot'),int): raise AdapterError('EXTENSION_RESERVATION_INVALID')
        return self.build_block_request(row,reservation['reserved_slot'],reservation.get('request_phase','EXTENSION_1'))

    def reduce(self, row: Mapping[str,object], create: Mapping[str,object], blocks: list[Mapping[str,object]]) -> dict:
        buys=[]; create_tx_index=None
        for block in blocks:
            for tx in block["target_relevant_transactions"]:
                if tx["signature"]==create["signature"]: create_tx_index=tx["transaction_index"]
                for event in tx["trade_events"]:
                    if event.get("mint")==row["mint"] and event.get("action_type")=="BUY" and tx["success"] and event.get("buyer")!=row["creator"]:
                        buys.append({"buyer":event["buyer"],"signature":tx["signature"],"slot":tx["slot"],"transaction_index":tx["transaction_index"],"event_index":event["event_log_index"],"event":event,"source_digest":block["source_digest"],**price_and_valuation(event)})
        buys.sort(key=lambda x:(x["slot"],x["transaction_index"],x["event_index"]))
        selected=buys[:3]
        if len(selected)<3:
            return {"terminal_status":"OPENING_HISTORY_INSUFFICIENT","needs_extension":len(blocks)<3,"opening_events":selected,"create_transaction_index":create_tx_index}
        for i,event in enumerate(selected,1): event["ordinal"]=i
        total=sum((Decimal(x.get("quote_sol","0")) for x in selected if x.get("price_status")=="PRICE_QUALIFIED"),Decimal(0))
        fp=list(self.profile.get("fingerprint",[])); status="FINGERPRINT_INSUFFICIENT" if len(fp)!=3 else ("FIXED_TEMPLATE_MATCH" if [x["buyer"] for x in selected]==fp else "FIXED_TEMPLATE_NON_MATCH")
        return {"terminal_status":"OPENING_HISTORY_QUALIFIED","needs_extension":False,"opening_events":selected,"opening_amount_sol":format(total,"f"),"post_B3_implied_valuation_sol":selected[2].get("post_buy_implied_market_cap_sol"),"post_B3_valuation_status":selected[2].get("valuation_status"),"fingerprint_status":status,"create_transaction_index":create_tx_index}

    def record(self,row:Mapping[str,object],create:Mapping[str,object],result:Mapping[str,object]) -> dict:
        events=[]
        for e in result.get("opening_events",[]):
            events.append({k:e.get(k) for k in ("ordinal","buyer","signature","slot","transaction_index","event_index","quote_lamports","quote_sol","token_raw","token_normalized","average_execution_price_sol_per_token","price_status","valuation_status","source_digest")})
        out={"record_version":"OPENING_HISTORY_CANONICAL_RECORD_V1","adapter_version":VERSION,"reducer_version":REDUCER_VERSION,"mint":row["mint"],"creator":row["creator"],"create":{**create,"transaction_index":result.get("create_transaction_index")},"B1_B2_B3":events,"opening_amount_sol":result.get("opening_amount_sol"),"post_B3_implied_valuation_sol":result.get("post_B3_implied_valuation_sol"),"post_B3_valuation_status":result.get("post_B3_valuation_status"),"fingerprint_status":result.get("fingerprint_status","FINGERPRINT_INSUFFICIENT"),"migration_status":row.get("migration_status"),"walkback_status":row.get("walkback_status"),"terminal_status":result["terminal_status"]}
        out["record_digest"]=sha256(canonical(out)).hexdigest(); return out

    def execute_row(self, executor, state: dict, row: Mapping[str, object]) -> dict:
        """Run one bounded row through the control plane; transport is injected."""
        mint=row["mint"]
        method,params=self.build_create_request(row)
        action=executor.dispatch(state,mint,"CREATE_TX",method,{"params":params})
        raw,meta=self.invoke(method,params); executor.failure_injector.hit('AFTER_CREATE_TRANSPORT_RETURN'); executor.consume(state,action,raw); payload=meta.pop("payload"); del raw
        create=self.normalize_create_response(row,payload); executor.failure_injector.hit('AFTER_CREATE_NORMALIZATION'); executor.commit_compact_handoff(state,action,"CREATE",create,16*1024); create=self.continue_from_create_handoff(row,create); executor.accept(state,action,"PENDING_CREATE_BLOCK")
        blocks=[]
        for offset in range(3):
            if offset:
                executor.reserve_extension(state,mint)
            slot=create["slot"]+offset; phase="CREATE_BLOCK" if offset==0 else f"EXTENSION_{offset}"
            method,params=self.build_block_request(row,slot,'CREATE_SLOT' if not offset else f'EXTENSION_{offset}'); action=executor.dispatch(state,mint,phase,method,{"params":params})
            raw,meta=self.invoke(method,params); executor.failure_injector.hit('AFTER_EXTENSION_TRANSPORT_RETURN' if offset else 'AFTER_BLOCK_TRANSPORT_RETURN'); executor.consume(state,action,raw); payload=meta.pop("payload")
            compact=self.normalize_block_response(row,slot,payload,meta["wire_bytes"]); executor.commit_compact_handoff(state,action,"BLOCK",compact,256*1024); del raw
            blocks.append(compact); result=self.reduce_opening_from_handoffs(row,create,blocks); executor.failure_injector.hit('AFTER_SEMANTIC_REDUCTION'); executor.failure_injector.hit('AFTER_B1_B2_B3_SELECTION'); executor.failure_injector.hit('AFTER_OPENING_AMOUNT')
            if not result["needs_extension"]: break
            executor.accept(state,action,f"PENDING_EXTENSION_{offset+1}")
        record=self.record(row,create,result); executor.commit_opening_reduction(state,mint,create,result,record); executor.commit_record(state,mint,record); return record
