"""Compact, operation-agnostic transaction ordering primitive."""
from __future__ import annotations

from typing import Mapping


ORDERING_VERSION = "TRANSACTION_ORDER_SLOT_ORDINAL_V1"


def compare(parent: Mapping, child: Mapping) -> dict:
    """Compare retained transaction evidence; never use time or signature order."""
    if parent.get("slot") is None or child.get("slot") is None:
        return {"state":"INSUFFICIENT_EVIDENCE","reason":"missing_slot"}
    if int(parent["slot"]) < int(child["slot"]): return {"state":"QUALIFIED","reason":"earlier_slot"}
    if int(parent["slot"]) > int(child["slot"]): return {"state":"CONFLICT","reason":"parent_later_slot"}
    if parent.get("transaction_order") is None or child.get("transaction_order") is None:
        return {"state":"INSUFFICIENT_EVIDENCE","reason":"same_slot_missing_transaction_ordinal"}
    if int(parent["transaction_order"]) < int(child["transaction_order"]): return {"state":"QUALIFIED","reason":"earlier_transaction_ordinal"}
    return {"state":"CONFLICT","reason":"parent_not_before_child"}
