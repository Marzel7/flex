"""Deterministic, operation-neutral EP1.3 fact normalizers."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

from .contracts import EvidenceProvenance, EvidenceRecord, FactFamily


PARSER_ID = "solana-json-rpc"
PARSER_VERSION = "1"
FACT_SCHEMA_VERSION = "1"
REPLAY_VERSION = "1"


def _pubkey(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        candidate = value.get("pubkey")
        return candidate if isinstance(candidate, str) else None
    return None


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_value(value: Any) -> Any:
    """Round-trip to the immutable JSON value domain."""
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


class AcquisitionNormalizer:
    def __init__(self, *, parser_id: str = PARSER_ID,
                 parser_version: str = PARSER_VERSION,
                 fact_schema_version: str = FACT_SCHEMA_VERSION,
                 replay_version: str = REPLAY_VERSION) -> None:
        self.parser_id = parser_id
        self.parser_version = parser_version
        self.fact_schema_version = fact_schema_version
        self.replay_version = replay_version

    def _record(self, family: FactFamily, natural_key: str,
                payload: Mapping[str, Any], envelope: Mapping[str, Any],
                *, observed_at: Optional[int] = None,
                parent_evidence_ids: Iterable[str] = ()) -> EvidenceRecord:
        acquisition = envelope.get("acquisition") or {}
        provenance = envelope.get("provenance") or {}
        source_metadata = provenance.get("source_metadata") or {}
        representation = (
            (envelope.get("artifact") or {}).get("representation")
            or acquisition.get("artifact_representation")
            or "CANONICALIZED_RESPONSE_REPRESENTATION"
        )
        quality = (
            "EXACT_PROVIDER_BYTES"
            if representation == "EXACT_PROVIDER_ARTIFACT"
            else "CANONICALIZED_LEGACY_REPRESENTATION"
        )
        return EvidenceRecord.create(
            family=family, chain="solana", network="mainnet-beta",
            natural_key=natural_key, payload=_json_value(dict(payload)),
            raw_artifact_digest=str((envelope.get("artifact") or {})["digest"]),
            observed_at=int(observed_at if observed_at is not None else envelope["observed_at"]),
            acquired_at=int(envelope["acquired_at"]), source_id=str(envelope["source"]),
            source_version=str(envelope["source_version"]),
            provider=str(envelope["provider"]),
            provider_request_id=provenance.get("provider_request_id"),
            parser_id=self.parser_id, parser_version=self.parser_version,
            replay_version=self.replay_version,
            verification_state=str(provenance.get("rpc_verification_state", "UNKNOWN")),
            provenance_quality=quality,
            provenance=EvidenceProvenance(
                endpoint_method=str(acquisition.get("method") or provenance.get("acquisition_method") or "UNKNOWN"),
                request_parameters_digest=str(source_metadata.get("request_digest") or "0" * 64),
                upstream_dependency=source_metadata.get("upstream_dependency"),
                acquisition_path=str(provenance.get("acquisition_method") or "UNKNOWN"),
                cache_source=str(acquisition.get("cache_state") or "unknown"),
                dependency_group=str(source_metadata.get("dependency_group") or envelope["provider"]),
                parent_evidence_ids=tuple(sorted(parent_evidence_ids)),
            ),
            fact_schema_version=self.fact_schema_version,
            created_at=int(envelope["acquired_at"]),
        )

    @staticmethod
    def decode_artifact(raw: bytes, envelope: Mapping[str, Any]) -> Any:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed JSON artifact: {exc}") from exc
        representation = (
            (envelope.get("artifact") or {}).get("representation")
            or (envelope.get("acquisition") or {}).get("artifact_representation")
        )
        if representation != "EXACT_PROVIDER_ARTIFACT" and isinstance(decoded, dict):
            if {"status", "data", "text", "headers"} <= set(decoded):
                if decoded.get("data") is not None:
                    return decoded["data"]
                text = decoded.get("text")
                if isinstance(text, str):
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
        return decoded

    def normalize(self, envelope: Mapping[str, Any], raw: bytes) -> list[EvidenceRecord]:
        payload_type = str(envelope.get("payload_type") or "")
        body = self.decode_artifact(raw, envelope)
        if payload_type == "external/registry":
            return self._external_registry(envelope, body)
        method = str((envelope.get("acquisition") or {}).get("method") or "")
        if method == "getTransaction":
            return self._transaction(envelope, body)
        if method == "getSignaturesForAddress":
            return self._address_history(envelope, body)
        raise NotImplementedError(f"unsupported acquisition method: {method or payload_type}")

    def _transaction(self, envelope: Mapping[str, Any], body: Any) -> list[EvidenceRecord]:
        if not isinstance(body, Mapping):
            raise ValueError("getTransaction artifact must be a JSON object")
        result = body.get("result")
        signatures = (envelope.get("acquisition") or {}).get("transaction_signatures") or []
        signature = next((x for x in signatures if isinstance(x, str)), None)
        if result is None:
            if not signature:
                raise ValueError("transaction verification lacks a subject signature")
            observation = self._record(
                FactFamily.TRANSACTION_VERIFICATION,
                f"verification/{signature}/{envelope['provider']}/{envelope['acquired_at']}",
                {"subject_evidence_id": None, "provider": envelope["provider"],
                 "verification_method": "getTransaction", "verification_result": "NOT_FOUND",
                 "finality": None, "checked_at": int(envelope["acquired_at"]),
                 "returned_artifact_digest": envelope["artifact"]["digest"], "error": body.get("error")},
                envelope,
            )
            return [observation]
        if not isinstance(result, Mapping):
            raise ValueError("getTransaction result must be an object or null")
        transaction = result.get("transaction") or {}
        message = transaction.get("message") or {}
        transaction_signatures = transaction.get("signatures") or []
        signature = signature or next((x for x in transaction_signatures if isinstance(x, str)), None)
        if not signature:
            raise ValueError("transaction signature unavailable")
        meta = result.get("meta") or {}
        account_values = list(message.get("accountKeys") or [])
        loaded = meta.get("loadedAddresses") or {}
        account_values.extend(loaded.get("writable") or [])
        account_values.extend(loaded.get("readonly") or [])
        accounts = [_pubkey(value) for value in account_values]
        fee_payer = accounts[0] if accounts else None
        tx_payload = {
            "signature": signature, "slot": _integer(result.get("slot")),
            "block_time": _integer(result.get("blockTime")), "success": meta.get("err") is None,
            "error": _json_value(meta.get("err")), "fee": _integer(meta.get("fee")),
            "fee_payer": fee_payer, "message_version": result.get("version"),
            "recent_blockhash": message.get("recentBlockhash"), "account_count": len(accounts),
            "instruction_count": len(message.get("instructions") or []),
            "inner_instructions_available": meta.get("innerInstructions") is not None,
            "logs_available": meta.get("logMessages") is not None,
            "confirmation_status": result.get("confirmationStatus"),
        }
        tx = self._record(FactFamily.TRANSACTION, f"transaction/{signature}", tx_payload,
                          envelope, observed_at=_integer(result.get("blockTime")))
        records = [tx]
        header = message.get("header") or {}
        required = _integer(header.get("numRequiredSignatures")) or 0
        readonly_signed = _integer(header.get("numReadonlySignedAccounts")) or 0
        readonly_unsigned = _integer(header.get("numReadonlyUnsignedAccounts")) or 0
        static_count = len(message.get("accountKeys") or [])
        for index, key in enumerate(accounts):
            if key is None:
                continue
            value = account_values[index]
            if isinstance(value, Mapping):
                signer = bool(value.get("signer"))
                writable = bool(value.get("writable"))
                source = str(value.get("source") or "static")
            else:
                signer = index < required
                writable = (index < max(0, required - readonly_signed)) or (
                    required <= index < max(required, static_count - readonly_unsigned)
                )
                source = "static" if index < static_count else "lookup_table"
            records.append(self._record(
                FactFamily.ACCOUNT_PARTICIPATION,
                f"account-participation/{signature}/{index}",
                {"signature": signature, "account_index": index, "public_key": key,
                 "is_signer": signer, "is_writable": writable,
                 "is_fee_payer": index == 0, "account_source": source,
                 "lookup_table_address": value.get("lookupTableAddress") if isinstance(value, Mapping) else None},
                envelope, observed_at=_integer(result.get("blockTime")),
                parent_evidence_ids=(tx.evidence_id,),
            ))
        signer_by_account = {
            str(record.payload["public_key"]): bool(record.payload["is_signer"])
            for record in records
            if record.fact_family == FactFamily.ACCOUNT_PARTICIPATION.value
        }
        instructions: list[tuple[int, Optional[int], Mapping[str, Any]]] = []
        for outer, item in enumerate(message.get("instructions") or []):
            if isinstance(item, Mapping):
                instructions.append((outer, None, item))
        for group in meta.get("innerInstructions") or []:
            if not isinstance(group, Mapping):
                continue
            outer = _integer(group.get("index"))
            if outer is None:
                continue
            for inner, item in enumerate(group.get("instructions") or []):
                if isinstance(item, Mapping):
                    instructions.append((outer, inner, item))
        for outer, inner, item in instructions:
            records.extend(self._instruction_records(
                envelope, tx, signature, accounts, result, outer, inner, item,
                signer_by_account,
            ))
        records.extend(self._balance_records(envelope, tx, signature, accounts, result, meta))
        verification = self._record(
            FactFamily.TRANSACTION_VERIFICATION,
            f"verification/{signature}/{envelope['provider']}/{envelope['acquired_at']}",
            {"subject_evidence_id": tx.evidence_id, "provider": envelope["provider"],
             "verification_method": "getTransaction", "verification_result": "VERIFIED",
             "finality": result.get("confirmationStatus"), "checked_at": int(envelope["acquired_at"]),
             "returned_artifact_digest": envelope["artifact"]["digest"], "error": body.get("error")},
            envelope, parent_evidence_ids=(tx.evidence_id,),
        )
        records.append(verification)
        return records

    def _instruction_records(self, envelope: Mapping[str, Any], tx: EvidenceRecord,
                             signature: str, accounts: list[Optional[str]], result: Mapping[str, Any],
                             outer: int, inner: Optional[int], item: Mapping[str, Any],
                             signer_by_account: Mapping[str, bool]) -> list[EvidenceRecord]:
        program_index = _integer(item.get("programIdIndex"))
        program_id = item.get("programId") or (
            accounts[program_index] if program_index is not None and program_index < len(accounts) else None
        )
        account_indexes = list(item.get("accounts") or [])
        parsed = item.get("parsed") if isinstance(item.get("parsed"), Mapping) else {}
        info = parsed.get("info") if isinstance(parsed.get("info"), Mapping) else {}
        parsed_type = parsed.get("type")
        position = f"{outer}:{inner if inner is not None else 'outer'}"
        instruction = self._record(
            FactFamily.INSTRUCTION, f"instruction/{signature}/{position}",
            {"signature": signature, "outer_instruction_index": outer,
             "inner_instruction_index": inner, "program_id": program_id,
             "account_indexes": account_indexes, "raw_instruction_data": item.get("data"),
             "parsed_instruction_type": parsed_type, "parsed_fields": _json_value(info),
             "execution_nesting_level": 1 if inner is None else 2},
            envelope, observed_at=_integer(result.get("blockTime")),
            parent_evidence_ids=(tx.evidence_id,),
        )
        records = [instruction]
        kind = str(parsed_type or "")
        if kind in {"transfer", "transferWithSeed"} and info.get("lamports") is not None:
            source, destination = info.get("source"), info.get("destination")
            amount = _integer(info.get("lamports"))
            if isinstance(source, str) and isinstance(destination, str) and amount is not None:
                records.append(self._record(
                    FactFamily.NATIVE_MOVEMENT,
                    f"native-movement/{signature}/{position}/{source}/{destination}/{amount}",
                    {"signature": signature, "instruction_position": position,
                     "source": source, "destination": destination,
                     "amount_lamports": amount, "program_id": program_id,
                     "authority": info.get("authority") or info.get("base"),
                     "decode_method": "parsed_instruction"}, envelope,
                    observed_at=_integer(result.get("blockTime")),
                    parent_evidence_ids=(instruction.evidence_id,),
                ))
        token_amount = info.get("tokenAmount") if isinstance(info.get("tokenAmount"), Mapping) else {}
        raw_token_amount = info.get("amount") if info.get("amount") is not None else token_amount.get("amount")
        if kind in {"transfer", "transferChecked"} and raw_token_amount is not None and info.get("lamports") is None:
            amount = str(raw_token_amount)
            records.append(self._record(
                FactFamily.TOKEN_MOVEMENT,
                f"token-movement/{signature}/{position}/{info.get('source')}/{info.get('destination')}/{amount}",
                {"signature": signature, "instruction_position": position,
                 "source_token_account": info.get("source"),
                 "destination_token_account": info.get("destination"),
                 "source_owner": info.get("sourceOwner"),
                 "destination_owner": info.get("destinationOwner"), "mint": info.get("mint"),
                 "raw_amount": amount, "decimals": _integer(info.get("decimals") if info.get("decimals") is not None else token_amount.get("decimals")),
                 "authority": info.get("authority"), "token_program": program_id},
                envelope, observed_at=_integer(result.get("blockTime")),
                parent_evidence_ids=(instruction.evidence_id,),
            ))
        if kind == "closeAccount":
            records.append(self._record(
                FactFamily.ACCOUNT_CLOSE, f"account-close/{signature}/{position}",
                {"signature": signature, "instruction_position": position, "program": program_id,
                 "closed_account": info.get("account"), "owner": info.get("owner"),
                 "close_authority": info.get("authority"), "close_destination": info.get("destination"),
                 "pre_close_balance": None, "returned_lamports": None, "token_mint": info.get("mint")},
                envelope, observed_at=_integer(result.get("blockTime")),
                parent_evidence_ids=(instruction.evidence_id,),
            ))
        lowered = kind.lower()
        if any(marker in lowered for marker in ("create", "initialize", "migrate")):
            event = self._record(
                FactFamily.PROGRAM_EVENT, f"program-event/{signature}/{position}/{kind}",
                {"signature": signature, "instruction_position": position, "program_id": program_id,
                 "event_discriminator": item.get("data"), "event_type": kind,
                 "event_payload": _json_value(info), "event_accounts": account_indexes,
                 "decoder_version": self.parser_version}, envelope,
                observed_at=_integer(result.get("blockTime")),
                parent_evidence_ids=(instruction.evidence_id,),
            )
            records.append(event)
            mint = info.get("mint")
            creator = info.get("creator") or info.get("user") or info.get("authority")
            if "create" in lowered and isinstance(mint, str) and isinstance(creator, str):
                creator_index = accounts.index(creator) if creator in accounts else None
                signer = signer_by_account.get(creator)
                records.append(self._record(
                    FactFamily.LAUNCH, f"launch/{program_id}/{signature}/{mint}",
                    {"mint": mint, "creation_signature": signature, "creation_instruction": position,
                     "creation_timestamp": _integer(result.get("blockTime")),
                     "creation_slot": _integer(result.get("slot")), "program_id": program_id,
                     "creator_account": creator, "creator_account_index": creator_index,
                     "creator_signer_state": signer, "fee_payer": accounts[0] if accounts else None,
                     "source_platform": str(program_id) if program_id else None}, envelope,
                    observed_at=_integer(result.get("blockTime")),
                    parent_evidence_ids=(event.evidence_id, instruction.evidence_id),
                ))
        return records

    def _balance_records(self, envelope: Mapping[str, Any], tx: EvidenceRecord,
                         signature: str, accounts: list[Optional[str]], result: Mapping[str, Any],
                         meta: Mapping[str, Any]) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        for index in range(min(len(accounts), max(len(pre), len(post)))):
            pre_value = _integer(pre[index]) if index < len(pre) else None
            post_value = _integer(post[index]) if index < len(post) else None
            records.append(self._record(
                FactFamily.BALANCE, f"balance/{signature}/{index}/native",
                {"signature": signature, "account": accounts[index], "account_index": index,
                 "asset_type": "native", "mint": None, "owner": None,
                 "pre_balance": pre_value, "post_balance": post_value,
                 "delta": (post_value - pre_value) if pre_value is not None and post_value is not None else None,
                 "decimals": 9, "source_availability": "provider_transaction_meta"}, envelope,
                observed_at=_integer(result.get("blockTime")), parent_evidence_ids=(tx.evidence_id,),
            ))
        token_entries: dict[tuple[int, str], dict[str, Any]] = {}
        for side, values in (("pre", meta.get("preTokenBalances") or []),
                             ("post", meta.get("postTokenBalances") or [])):
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                index = _integer(value.get("accountIndex"))
                mint = value.get("mint")
                if index is None or not isinstance(mint, str):
                    continue
                entry = token_entries.setdefault((index, mint), {"owner": value.get("owner"), "decimals": None})
                amount = value.get("uiTokenAmount") or {}
                entry[side] = _integer(amount.get("amount"))
                entry["decimals"] = _integer(amount.get("decimals"))
        for (index, mint), value in sorted(token_entries.items()):
            before, after = value.get("pre"), value.get("post")
            records.append(self._record(
                FactFamily.BALANCE, f"balance/{signature}/{index}/token/{mint}",
                {"signature": signature, "account": accounts[index] if index < len(accounts) else None,
                 "account_index": index, "asset_type": "token", "mint": mint,
                 "owner": value.get("owner"), "pre_balance": before, "post_balance": after,
                 "delta": (after - before) if before is not None and after is not None else None,
                 "decimals": value.get("decimals"), "source_availability": "provider_token_balances"},
                envelope, observed_at=_integer(result.get("blockTime")), parent_evidence_ids=(tx.evidence_id,),
            ))
        return records

    def _address_history(self, envelope: Mapping[str, Any], body: Any) -> list[EvidenceRecord]:
        result = body.get("result") if isinstance(body, Mapping) else None
        if not isinstance(result, list):
            raise ValueError("address-history result must be a list")
        acquisition = envelope.get("acquisition") or {}
        address = acquisition.get("creator") or acquisition.get("address")
        if not isinstance(address, str):
            raise ValueError("address-history envelope lacks queried address")
        returned = [item.get("signature") for item in result if isinstance(item, Mapping) and isinstance(item.get("signature"), str)]
        request_digest = (envelope.get("provenance") or {}).get("source_metadata", {}).get("request_digest")
        return [self._record(
            FactFamily.ADDRESS_HISTORY,
            f"address-history/{envelope['provider']}/{address}/{request_digest}/{envelope['acquired_at']}/{envelope['artifact']['digest']}",
            {"address": address, "endpoint_method": "getSignaturesForAddress",
             "before_cursor": acquisition.get("cursor"), "until_cursor": acquisition.get("until_cursor"),
             "minimum_context_slot": acquisition.get("minimum_context_slot"),
             "page_size": acquisition.get("page_size"), "returned_signatures": returned,
             "returned_count": len(returned), "page_complete": acquisition.get("page_complete"),
             "provider_coverage_statement": acquisition.get("provider_coverage_statement"),
             "acquisition_timestamp": int(envelope["acquired_at"])}, envelope,
        )]

    def _external_registry(self, envelope: Mapping[str, Any], body: Any) -> list[EvidenceRecord]:
        if not isinstance(body, Mapping):
            raise ValueError("external registry artifact must be an object")
        required = ("subject", "claimed_label", "registry", "registry_version")
        if any(not isinstance(body.get(key), str) for key in required):
            raise ValueError("external registry artifact lacks required claim fields")
        natural = f"external-registry/{body['registry']}/{body['registry_version']}/{body['subject']}/{envelope['artifact']['digest']}"
        payload = {key: body.get(key) for key in (
            "subject", "claimed_label", "registry", "registry_version", "source_url",
            "document_digest", "valid_from", "valid_to", "observed_at"
        )}
        return [self._record(FactFamily.EXTERNAL_REGISTRY, natural, payload, envelope)]
