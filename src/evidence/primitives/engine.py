"""Deterministic Evidence-only Primitive Contract v1 engine."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from ..contracts import canonical_json_bytes
from ..database import EvidenceDatabase
from ..metrics import EvidenceMetrics
from .contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)
from .registry import PrimitiveRegistry


WSOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class _Fact:
    evidence_id: str
    logical_fact_id: str
    family: str
    payload: Mapping[str, Any]
    observed_at: int
    payload_digest: str


class _Index:
    def __init__(self, rows: Iterable[sqlite3.Row]) -> None:
        self.all: list[_Fact] = []
        self.by_family: dict[str, list[_Fact]] = defaultdict(list)
        self.by_evidence_id: dict[str, _Fact] = {}
        self.evidence_order: dict[str, int] = {}
        self._matching: dict[tuple[str, str], dict[Any, list[_Fact]]] = {}
        for row in rows:
            fact = _Fact(
                evidence_id=row["evidence_id"], logical_fact_id=row["logical_fact_id"],
                family=row["fact_family"], payload=json.loads(row["payload_json"]),
                observed_at=row["observed_at"], payload_digest=row["payload_digest"],
            )
            self.all.append(fact)
            self.by_family[fact.family].append(fact)
            self.by_evidence_id[fact.evidence_id] = fact
            self.evidence_order[fact.evidence_id] = len(self.all) - 1
        grouped: dict[str, set[str]] = defaultdict(set)
        grouped_ids: dict[str, set[str]] = defaultdict(set)
        for fact in self.all:
            grouped[fact.logical_fact_id].add(fact.payload_digest)
            grouped_ids[fact.logical_fact_id].add(fact.evidence_id)
        self.conflicting_evidence_ids = {
            evidence_id
            for logical_id, digests in grouped.items() if len(digests) > 1
            for evidence_id in grouped_ids[logical_id]
        }

    def family(self, name: str) -> list[_Fact]:
        return self.by_family.get(name, [])

    def matching(self, name: str, key: str, value: Any) -> list[_Fact]:
        cache_key = (name, key)
        if cache_key not in self._matching:
            values: dict[Any, list[_Fact]] = defaultdict(list)
            for fact in self.family(name):
                candidate = fact.payload.get(key)
                try:
                    values[candidate].append(fact)
                except TypeError:
                    # Complex JSON values are not used by current Primitive v1
                    # lookups; preserve the former equality semantics if added.
                    continue
            self._matching[cache_key] = values
        try:
            return self._matching[cache_key].get(value, [])
        except TypeError:
            return [fact for fact in self.family(name) if fact.payload.get(key) == value]


class PrimitiveEngine:
    """Pure Evidence-to-Primitive transformation plus append-only persistence."""

    VERSION = PrimitiveRegistry.VERSION

    def __init__(self, database: EvidenceDatabase, *, version: str = VERSION,
                 metrics: EvidenceMetrics | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.database = database
        self.version = version
        self.metrics = metrics or EvidenceMetrics()
        self.clock = clock

    def _observation(self, primitive_type: PrimitiveType, facts: Iterable[_Fact],
                     subjects: Iterable[str], output: Mapping[str, Any],
                     *, parameters: Mapping[str, Any] | None = None,
                     start: Optional[int] = None, end: Optional[int] = None,
                     quality: PrimitiveQuality = PrimitiveQuality.PROVEN,
                     missing: Iterable[str] = (), failure: Optional[str] = None) -> PrimitiveObservation:
        return PrimitiveObservation.create(
            primitive_type=primitive_type, primitive_version=self.version,
            evidence_ids=[fact.evidence_id for fact in facts], subjects=list(subjects),
            parameters=dict(parameters or {}),
            observation_window=ObservationWindow(start=start, end=end),
            output_payload=dict(output), quality_state=quality,
            missing_inputs=list(missing), failure_state=failure,
            generated_at=int(self.clock()),
        )

    @staticmethod
    def _timestamp(fact: _Fact) -> int:
        value = fact.payload.get("block_time")
        return int(value if value is not None else fact.observed_at)

    def generate(self, rows: Iterable[sqlite3.Row]) -> list[PrimitiveObservation]:
        index = _Index(rows)
        observations: list[PrimitiveObservation] = []
        base_generators = (
            self._system_transfers, self._launch_signers, self._wsol_closes,
            self._direct_counterparties, self._program_interactions,
            self._wallet_freshness, self._shared_transactions,
        )
        for generator in base_generators:
            try:
                observations.extend(generator(index))
            except Exception:
                self.metrics.increment("primitive_failures")
        derived_generators = (
            lambda: self._launch_activations(index, observations),
            lambda: self._economic_funding(index, observations),
            lambda: self._repeated_counterparties(observations),
            lambda: self._behavioural_timing(observations),
        )
        for generator in derived_generators:
            try:
                observations.extend(generator())
            except Exception:
                self.metrics.increment("primitive_failures")
        observations = [self._mark_conflicting(item, index) for item in observations]
        unique = {item.primitive_id: item for item in observations}
        return [unique[key] for key in sorted(unique)]

    def _mark_conflicting(self, item: PrimitiveObservation,
                          index: _Index) -> PrimitiveObservation:
        if not set(item.evidence_ids).intersection(index.conflicting_evidence_ids):
            return item
        return PrimitiveObservation.create(
            primitive_type=PrimitiveType(item.primitive_type),
            primitive_version=item.primitive_version,
            evidence_ids=item.evidence_ids, subjects=item.subjects,
            parameters=item.parameters, observation_window=item.observation_window,
            output_payload=item.output_payload,
            quality_state=PrimitiveQuality.CONFLICTING,
            missing_inputs=item.missing_inputs,
            failure_state="CONFLICTING_EVIDENCE_OBSERVATIONS",
            generated_at=item.generated_at,
        )

    def run_once(self) -> dict[str, int]:
        started = time.monotonic()
        rows = self.database.load_normalized_records()
        input_digest = hashlib.sha256(canonical_json_bytes([
            [row["evidence_id"], row["payload_digest"]] for row in rows
        ])).hexdigest()
        observations = self.generate(rows)
        result = self.database.append_primitives(observations)
        if result["duplicates"]:
            self.metrics.increment("primitive_replay", result["duplicates"])
        self.metrics.increment("primitive_generated", len(observations))
        self.metrics.increment("primitive_inserted", result["inserted"])
        self.metrics.observe("primitive_latency_ms", (time.monotonic() - started) * 1000)
        for item in observations:
            self.metrics.increment(f"primitive_quality_{item.quality_state.lower()}")
            self.metrics.increment(f"primitive_type_{item.primitive_type.lower()}")
        return {**result, "generated": len(observations), "input_digest": input_digest}

    def _system_transfers(self, index: _Index) -> list[PrimitiveObservation]:
        result = []
        for instruction in index.family("InstructionFact"):
            info = instruction.payload.get("parsed_fields") or {}
            if instruction.payload.get("parsed_instruction_type") not in {"transfer", "transferWithSeed"}:
                continue
            if info.get("lamports") is None or not isinstance(info.get("source"), str) or not isinstance(info.get("destination"), str):
                continue
            signature = instruction.payload["signature"]
            transactions = index.matching("TransactionFact", "signature", signature)
            participants = [
                fact for fact in index.matching("AccountParticipationFact", "signature", signature)
                if fact.payload.get("public_key") in {info["source"], info["destination"]}
            ]
            quality = PrimitiveQuality.PROVEN if transactions and len(participants) == 2 else PrimitiveQuality.INCOMPLETE
            missing = []
            if not transactions: missing.append("TransactionFact")
            if len(participants) != 2: missing.append("AccountParticipationFact")
            timestamp = self._timestamp(transactions[0]) if transactions else instruction.observed_at
            result.append(self._observation(
                PrimitiveType.SYSTEM_TRANSFER, [instruction, *transactions, *participants],
                [info["source"], info["destination"]],
                {"source": info["source"], "destination": info["destination"],
                 "amount": int(info["lamports"]), "signature": signature,
                 "program": instruction.payload.get("program_id"), "timestamp": timestamp},
                start=timestamp, end=timestamp, quality=quality, missing=missing,
                failure="MISSING_REQUIRED_EVIDENCE" if missing else None,
            ))
        return result

    def _launch_signers(self, index: _Index) -> list[PrimitiveObservation]:
        result = []
        for launch in index.family("LaunchFact"):
            signature = launch.payload["creation_signature"]
            wallet = launch.payload["creator_account"]
            participants = [
                fact for fact in index.matching("AccountParticipationFact", "signature", signature)
                if fact.payload.get("public_key") == wallet
            ]
            signer = participants[0].payload.get("is_signer") if participants else None
            quality = (PrimitiveQuality.PROVEN if signer is True else
                       PrimitiveQuality.DISPROVEN if signer is False else PrimitiveQuality.INCOMPLETE)
            result.append(self._observation(
                PrimitiveType.LAUNCH_SIGNER, [launch, *participants], [wallet, launch.payload["mint"]],
                {"mint": launch.payload["mint"], "launch_signature": signature,
                 "wallet": wallet, "signer": signer},
                start=launch.payload.get("creation_timestamp"),
                end=launch.payload.get("creation_timestamp"), quality=quality,
                missing=() if participants else ("AccountParticipationFact",),
                failure=None if participants else "ACCOUNT_PARTICIPATION_UNAVAILABLE",
            ))
        return result

    def _wsol_closes(self, index: _Index) -> list[PrimitiveObservation]:
        result = []
        registry_wsol = {
            fact.payload.get("subject") for fact in index.family("ExternalRegistryObservation")
            if str(fact.payload.get("claimed_label", "")).upper() in {"WSOL", "WRAPPED SOL"}
        }
        for close in index.family("AccountCloseFact"):
            payload = close.payload
            mint = payload.get("token_mint")
            registry = [fact for fact in index.family("ExternalRegistryObservation") if fact.payload.get("subject") == mint]
            established = mint == WSOL_MINT or mint in registry_wsol
            quality = PrimitiveQuality.PROVEN if established else PrimitiveQuality.INCOMPLETE
            result.append(self._observation(
                PrimitiveType.WSOL_CLOSE, [close, *registry],
                [value for value in (payload.get("closed_account"), payload.get("owner"), payload.get("close_destination")) if isinstance(value, str)],
                {"temporary_wsol_account": payload.get("closed_account"), "owner": payload.get("owner"),
                 "close_authority": payload.get("close_authority"), "destination": payload.get("close_destination"),
                 "returned_amount": payload.get("returned_lamports"), "signature": payload.get("signature")},
                start=close.observed_at, end=close.observed_at, quality=quality,
                missing=() if established else ("WSOL mint evidence",),
                failure=None if established else "MINT_UNESTABLISHED",
            ))
        return result

    def _direct_counterparties(self, index: _Index) -> list[PrimitiveObservation]:
        result = []
        for family in ("NativeMovementFact", "TokenMovementFact"):
            for fact in index.family(family):
                payload = fact.payload
                if family == "NativeMovementFact":
                    source, destination = payload.get("source"), payload.get("destination")
                    asset, amount = "SOL", payload.get("amount_lamports")
                else:
                    source = payload.get("source_owner") or payload.get("source_token_account")
                    destination = payload.get("destination_owner") or payload.get("destination_token_account")
                    asset, amount = payload.get("mint"), payload.get("raw_amount")
                if not isinstance(source, str) or not isinstance(destination, str):
                    continue
                result.append(self._observation(
                    PrimitiveType.DIRECT_COUNTERPARTY, [fact], [source, destination],
                    {"source": source, "destination": destination, "asset": asset,
                     "amount": amount, "signature": payload.get("signature"),
                     "direction": "SOURCE_TO_DESTINATION"},
                    start=fact.observed_at, end=fact.observed_at,
                ))
        return result

    def _program_interactions(self, index: _Index) -> list[PrimitiveObservation]:
        grouped: dict[tuple[str, str, str], list[tuple[_Fact, _Fact]]] = defaultdict(list)
        for instruction in index.family("InstructionFact"):
            signature = instruction.payload.get("signature")
            program = instruction.payload.get("program_id")
            indexes = set(instruction.payload.get("account_indexes") or [])
            if not isinstance(signature, str) or not isinstance(program, str):
                continue
            for participant in index.matching("AccountParticipationFact", "signature", signature):
                if participant.payload.get("account_index") in indexes:
                    wallet = participant.payload.get("public_key")
                    if isinstance(wallet, str): grouped[(signature, program, wallet)].append((instruction, participant))
        result = []
        for (signature, program, wallet), pairs in sorted(grouped.items()):
            facts = [fact for pair in pairs for fact in pair]
            participant = pairs[0][1]
            result.append(self._observation(
                PrimitiveType.PROGRAM_INTERACTION, facts, [wallet, program],
                {"wallet": wallet, "program": program, "signature": signature,
                 "signed": participant.payload.get("is_signer"),
                 "writable": participant.payload.get("is_writable"),
                 "instruction_count": len(pairs)},
                start=min(fact.observed_at for fact in facts),
                end=max(fact.observed_at for fact in facts),
            ))
        return result

    def _wallet_freshness(self, index: _Index) -> list[PrimitiveObservation]:
        policy = {"permitted_prior_transaction_count": 0, "required_zero_balance": True,
                  "require_complete_history": True,
                  "history_order": "NEWEST_FIRST",
                  "reference_boundary": "STRICTLY_PRECEDING"}
        histories = index.family("AddressHistoryObservation")
        participants = index.family("AccountParticipationFact")
        histories_by_wallet: dict[str, list[_Fact]] = defaultdict(list)
        participants_by_event: dict[tuple[str, str], list[_Fact]] = defaultdict(list)
        for fact in histories:
            address = fact.payload.get("address")
            if isinstance(address, str):
                histories_by_wallet[address].append(fact)
        for fact in participants:
            participant_signature = fact.payload.get("signature")
            public_key = fact.payload.get("public_key")
            if isinstance(participant_signature, str) and isinstance(public_key, str):
                participants_by_event[(participant_signature, public_key)].append(fact)
        result = []
        for balance in index.family("BalanceFact"):
            if balance.payload.get("asset_type") != "native": continue
            wallet = balance.payload.get("account")
            signature = balance.payload.get("signature")
            if not isinstance(wallet, str) or not isinstance(signature, str): continue
            relevant_history = sorted(
                histories_by_wallet.get(wallet, ()),
                key=lambda fact: fact.evidence_id,
            )
            relevant_participants = participants_by_event.get((signature, wallet), [])
            missing = []
            if not relevant_history: missing.append("AddressHistoryObservation")
            if not relevant_participants: missing.append("AccountParticipationFact")
            if balance.payload.get("pre_balance") is None: missing.append("BalanceFact.pre_balance")
            state = "UNKNOWN"
            quality = PrimitiveQuality.INCOMPLETE if missing else PrimitiveQuality.PROVEN
            if not missing:
                containing_reference = [
                    fact for fact in relevant_history
                    if signature in fact.payload.get("returned_signatures", [])
                ]
                if not containing_reference:
                    state = "UNKNOWN"
                    quality = PrimitiveQuality.UNVERIFIABLE
                    missing.append("AddressHistoryObservation.reference_event")
                else:
                    # getSignaturesForAddress observations retain provider order:
                    # newest first.  Only entries after the immutable reference
                    # event are therefore historical predecessors.  Entries
                    # before it are later activity and must not affect the
                    # historical freshness decision.
                    history = containing_reference[0].payload
                    returned = history.get("returned_signatures", [])
                    reference_index = returned.index(signature)
                    prior = returned[reference_index + 1:]
                    complete = history.get("page_complete") is True or not prior
                    if balance.payload.get("pre_balance") == 0 and complete and not prior:
                        state = "VERIFIED_FRESH"
                    elif balance.payload.get("pre_balance") != 0 or prior:
                        state = "NOT_FRESH"
                    else:
                        quality = PrimitiveQuality.UNVERIFIABLE
            result.append(self._observation(
                PrimitiveType.WALLET_FRESH_AT_EVENT,
                [balance, *relevant_history, *relevant_participants], [wallet],
                {"wallet": wallet, "reference_event": signature, "freshness_state": state},
                parameters=policy, start=balance.observed_at, end=balance.observed_at,
                quality=quality, missing=missing,
                failure=("MISSING_REFERENCE_EVENT" if
                         "AddressHistoryObservation.reference_event" in missing else
                         "MISSING_REQUIRED_EVIDENCE" if missing else None),
            ))
        return result

    def _shared_transactions(self, index: _Index) -> list[PrimitiveObservation]:
        result = []
        for transaction in index.family("TransactionFact"):
            signature = transaction.payload.get("signature")
            participants = index.matching("AccountParticipationFact", "signature", signature)
            if len(participants) < 2: continue
            roles = [{"wallet": fact.payload["public_key"], "signer": fact.payload["is_signer"],
                      "writable": fact.payload["is_writable"], "fee_payer": fact.payload["is_fee_payer"]}
                     for fact in participants]
            wallets = [item["wallet"] for item in roles]
            timestamp = self._timestamp(transaction)
            result.append(self._observation(
                PrimitiveType.SHARED_TRANSACTION, [transaction, *participants], wallets,
                {"wallets": sorted(wallets), "signature": signature,
                 "roles": sorted(roles, key=lambda item: item["wallet"])},
                start=timestamp, end=timestamp,
            ))
        return result

    def _launch_activations(self, index: _Index, primitives: list[PrimitiveObservation]) -> list[PrimitiveObservation]:
        transfers = [item for item in primitives if item.primitive_type == PrimitiveType.SYSTEM_TRANSFER.value]
        freshness = [item for item in primitives if item.primitive_type == PrimitiveType.WALLET_FRESH_AT_EVENT.value]
        transfers_by_destination: dict[str, list[PrimitiveObservation]] = defaultdict(list)
        signers_by_mint: dict[str, list[PrimitiveObservation]] = defaultdict(list)
        freshness_by_event: dict[tuple[str, str], PrimitiveObservation] = {}
        for item in transfers:
            transfers_by_destination[str(item.output_payload.get("destination"))].append(item)
        for item in primitives:
            if item.primitive_type == PrimitiveType.LAUNCH_SIGNER.value:
                signers_by_mint[str(item.output_payload.get("mint"))].append(item)
        for item in freshness:
            for subject in item.subjects:
                freshness_by_event[(subject, str(item.output_payload.get("reference_event")))] = item
        result = []
        for launch in index.family("LaunchFact"):
            creator = launch.payload.get("creator_account")
            launch_time = launch.payload.get("creation_timestamp")
            signers = signers_by_mint.get(str(launch.payload.get("mint")), [])
            for transfer in transfers_by_destination.get(str(creator), []):
                funding_time = transfer.output_payload.get("timestamp")
                latency = launch_time - funding_time if launch_time is not None and funding_time is not None else None
                fresh = freshness_by_event.get((str(creator), str(transfer.output_payload.get("signature"))))
                evidence = [launch]
                evidence.extend(self._facts_for_ids(index, transfer.evidence_ids))
                for signer in signers: evidence.extend(self._facts_for_ids(index, signer.evidence_ids))
                if fresh: evidence.extend(self._facts_for_ids(index, fresh.evidence_ids))
                missing = []
                if not signers: missing.append("LAUNCH_SIGNER")
                if fresh is None: missing.append("WALLET_FRESH_AT_EVENT")
                result.append(self._observation(
                    PrimitiveType.LAUNCH_ACTIVATION, evidence,
                    [value for value in (transfer.output_payload.get("source"), creator, launch.payload.get("mint")) if isinstance(value, str)],
                    {"activation_sender": transfer.output_payload.get("source"), "creator": creator,
                     "mint": launch.payload.get("mint"), "amount": transfer.output_payload.get("amount"),
                     "funding_signature": transfer.output_payload.get("signature"),
                     "launch_signature": launch.payload.get("creation_signature"), "latency": latency,
                     "freshness_state": fresh.output_payload.get("freshness_state") if fresh else "UNKNOWN"},
                    start=funding_time, end=launch_time,
                    quality=PrimitiveQuality.PROVEN if not missing else PrimitiveQuality.INCOMPLETE,
                    missing=missing, failure="MISSING_REQUIRED_PRIMITIVE" if missing else None,
                ))
        return result

    def _economic_funding(self, index: _Index, primitives: list[PrimitiveObservation]) -> list[PrimitiveObservation]:
        transfers = [item for item in primitives if item.primitive_type == PrimitiveType.DIRECT_COUNTERPARTY.value]
        transfers_by_destination: dict[str, list[PrimitiveObservation]] = defaultdict(list)
        for item in transfers:
            transfers_by_destination[str(item.output_payload.get("destination"))].append(item)
        activations = {item.output_payload.get("funding_signature") for item in primitives if item.primitive_type == PrimitiveType.LAUNCH_ACTIVATION.value}
        result = []
        parameters = {"amount_policy": "UNFILTERED", "recipient_policy": "LAUNCH_CREATOR"}
        for launch in index.family("LaunchFact"):
            creator, launch_time = launch.payload.get("creator_account"), launch.payload.get("creation_timestamp")
            for transfer in transfers_by_destination.get(str(creator), []):
                timestamp = transfer.observation_window.start
                signature = transfer.output_payload.get("signature")
                result.append(self._observation(
                    PrimitiveType.ECONOMIC_FUNDING,
                    [launch, *self._facts_for_ids(index, transfer.evidence_ids)],
                    [value for value in (transfer.output_payload.get("source"), creator, launch.payload.get("mint")) if isinstance(value, str)],
                    {"funder": transfer.output_payload.get("source"), "recipient": creator,
                     "amount": transfer.output_payload.get("amount"), "asset": transfer.output_payload.get("asset"),
                     "signature": signature,
                     "time_relative_to_launch": launch_time - timestamp if launch_time is not None and timestamp is not None else None,
                     "distinct_from_activation_transaction": signature not in activations},
                    parameters=parameters, start=timestamp, end=launch_time,
                ))
        return result

    def _repeated_counterparties(self, primitives: list[PrimitiveObservation]) -> list[PrimitiveObservation]:
        grouped: dict[tuple[str, str], list[PrimitiveObservation]] = defaultdict(list)
        for item in primitives:
            if item.primitive_type == PrimitiveType.DIRECT_COUNTERPARTY.value:
                grouped[(str(item.output_payload.get("source")), str(item.output_payload.get("destination")))].append(item)
        result = []
        for (source, destination), items in sorted(grouped.items()):
            signatures = {item.output_payload.get("signature") for item in items}
            if len(signatures) < 2: continue
            starts = [item.observation_window.start for item in items if item.observation_window.start is not None]
            evidence = sorted({value for item in items for value in item.evidence_ids})
            result.append(PrimitiveObservation.create(
                primitive_type=PrimitiveType.REPEATED_COUNTERPARTY,
                primitive_version=self.version, evidence_ids=evidence,
                subjects=[source, destination], parameters={"minimum_count": 2},
                observation_window=ObservationWindow(min(starts) if starts else None, max(starts) if starts else None),
                output_payload={"source": source, "destination": destination,
                                "transaction_count": len(signatures),
                                "first_observed": min(starts) if starts else None,
                                "last_observed": max(starts) if starts else None},
                quality_state=PrimitiveQuality.PROVEN, generated_at=int(self.clock()),
            ))
        return result

    def _behavioural_timing(self, primitives: list[PrimitiveObservation]) -> list[PrimitiveObservation]:
        grouped: dict[str, list[PrimitiveObservation]] = defaultdict(list)
        for item in primitives:
            if item.observation_window.start is None: continue
            for subject in item.subjects: grouped[subject].append(item)
        result = []
        for subject, items in sorted(grouped.items()):
            ordered = sorted(items, key=lambda item: (item.observation_window.start or 0, item.primitive_id))
            if len(ordered) < 2: continue
            timestamps = [item.observation_window.start for item in ordered if item.observation_window.start is not None]
            deltas = [timestamps[index] - timestamps[index - 1] for index in range(1, len(timestamps))]
            evidence = sorted({value for item in ordered for value in item.evidence_ids})
            result.append(PrimitiveObservation.create(
                primitive_type=PrimitiveType.BEHAVIOURAL_TIMING,
                primitive_version=self.version, evidence_ids=evidence, subjects=[subject],
                parameters={"ordering": "CHAIN_TIMESTAMP", "event_scope": "ALL_PRIMITIVE_V1"},
                observation_window=ObservationWindow(min(timestamps), max(timestamps)),
                output_payload={"event_types": [item.primitive_type for item in ordered],
                                "time_deltas": deltas, "sample_count": len(ordered),
                                "window": {"start": min(timestamps), "end": max(timestamps)}},
                quality_state=PrimitiveQuality.PROVEN, generated_at=int(self.clock()),
            ))
        return result

    @staticmethod
    def _facts_for_ids(index: _Index, evidence_ids: Iterable[str]) -> list[_Fact]:
        wanted = {value for value in evidence_ids if value in index.by_evidence_id}
        return [index.by_evidence_id[value]
                for value in sorted(wanted, key=index.evidence_order.__getitem__)]

    def health(self) -> dict[str, Any]:
        connection = self.database.connection
        owned = connection
        if connection is None:
            if not self.database.path.exists(): return {"status": "NOT_INITIALIZED"}
            connection = sqlite3.connect(f"file:{self.database.path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        counts = {row["quality_state"]: row["count"] for row in connection.execute(
            "SELECT quality_state,COUNT(*) AS count FROM primitive_observations GROUP BY quality_state"
        )}
        versions = [dict(row) for row in connection.execute(
            "SELECT primitive_type,primitive_version,COUNT(*) AS count FROM primitive_observations "
            "GROUP BY primitive_type,primitive_version ORDER BY primitive_type,primitive_version"
        )]
        evidence_count = connection.execute("SELECT COUNT(*) FROM normalized_evidence_records").fetchone()[0]
        primitive_input_count = connection.execute("SELECT COUNT(DISTINCT evidence_id) FROM primitive_evidence_inputs").fetchone()[0]
        result = {"status": "HEALTHY", "primitive_backlog": max(0, evidence_count - primitive_input_count),
                  "quality_states": counts, "versions": versions, "metrics": self.metrics.snapshot()}
        if owned is None: connection.close()
        return result
