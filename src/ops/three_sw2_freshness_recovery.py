"""EP3.2B bounded reference-event recovery for four frozen 3SW2 creators."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Mapping

import aiohttp

from src.acquisition.transaction import SharedTransactionAcquisition
from src.evidence.service import EvidencePlatform
from src.ops.three_sw2_shadow_corpus import ThreeSw2ShadowCorpus
from src.ops.watchtower_shadow_corpus import _read_only
from src.ops.watchtower_shadow_recovery import (
    RecoveryBudget, RecoveryLimits, WatchtowerShadowRecovery,
)


MAX_ADDITIONAL_PAGES = 10
EXPECTED_RPC = 4
HARD_RPC_CEILING = 4 * MAX_ADDITIONAL_PAGES
HARD_CREDIT_CEILING = 10 * HARD_RPC_CEILING


class ThreeSw2FreshnessRecovery:
    """Append only the address-history pages needed to reach four references."""

    def __init__(self, *, operations_db: Path, main_db: Path, cache_db: Path,
                 output_root: Path) -> None:
        self.corpus = ThreeSw2ShadowCorpus(
            operations_db=operations_db, main_db=main_db, cache_db=cache_db,
            output_root=output_root,
        )
        self.operations_db = Path(operations_db)
        self.main_db = Path(main_db)
        self.cache_db = Path(cache_db)
        self.output_root = Path(output_root)

    def census(self) -> list[dict[str, Any]]:
        population = self.corpus.population()
        edge_by_creator = {
            row["to_wallet"]: row for row in population["provisioning_edges"]
        }
        connection = _read_only(self.output_root / "evidence.db")
        try:
            histories: dict[str, list[dict[str, Any]]] = {}
            for (raw,) in connection.execute(
                "SELECT payload_json FROM normalized_evidence_records "
                "WHERE fact_family='AddressHistoryObservation'"
            ):
                payload = json.loads(raw)
                histories.setdefault(str(payload.get("address")), []).append(payload)
        finally:
            connection.close()
        unresolved = []
        for creator, edge in sorted(edge_by_creator.items()):
            reference = str(edge["funding_tx_signature"])
            observations = histories.get(creator, [])
            if not observations:
                raise RuntimeError(f"frozen creator lacks initial history: {creator}")
            initial = sorted(
                observations, key=lambda item: int(item.get("acquisition_timestamp") or 0)
            )[0]
            signatures = list(initial.get("returned_signatures") or [])
            if reference in signatures:
                continue
            if not signatures:
                raise RuntimeError(f"frozen creator history is empty: {creator}")
            unresolved.append({
                "creator": creator,
                "mint": edge["source_mint"],
                "activation_reference": reference,
                "activation_timestamp": edge["funding_block_time"],
                "current_evidence": f"AddressHistoryObservation({len(signatures)} signatures)",
                "current_primitive": "UNKNOWN / UNVERIFIABLE",
                "reason": "Activation reference absent from retained bounded history",
                "required_observation": "Address-history page containing activation reference",
                "next_cursor": signatures[-1],
            })
        if len(unresolved) != 4:
            raise RuntimeError(
                f"EP3.2B boundary requires exactly four unresolved creators, found {len(unresolved)}"
            )
        return unresolved

    def _reference_present(self, creator: str, reference: str) -> bool:
        connection = _read_only(self.output_root / "evidence.db")
        try:
            for (raw,) in connection.execute(
                "SELECT payload_json FROM normalized_evidence_records "
                "WHERE fact_family='AddressHistoryObservation'"
            ):
                payload = json.loads(raw)
                if (payload.get("address") == creator and
                        reference in payload.get("returned_signatures", [])):
                    return True
            return False
        finally:
            connection.close()

    def plan(self) -> dict[str, Any]:
        census = self.census()
        return {
            "milestone": "EP3.2B",
            "population": {"creators": 4, "launches": 4},
            "subjects": census,
            "transaction_fetches": 0,
            "minimum_address_history_pages": 4,
            "expected_rpc_calls": EXPECTED_RPC,
            "expected_credits": EXPECTED_RPC * 10,
            "maximum_additional_pages_per_creator": MAX_ADDITIONAL_PAGES,
            "hard_rpc_ceiling": HARD_RPC_CEILING,
            "hard_credit_ceiling": HARD_CREDIT_CEILING,
            "population_expansion": False,
        }

    async def run(self) -> dict[str, Any]:
        started = time.monotonic()
        plan = self.plan()
        self.corpus.helper._write_json(self.output_root / "ep3_2b_acquisition_plan.json", plan)
        limits = RecoveryLimits(
            known_transactions=0, discovery_subjects=4,
            pages_per_subject=MAX_ADDITIONAL_PAGES,
            rpc_calls=HARD_RPC_CEILING, credits=HARD_CREDIT_CEILING,
        )
        config = self.corpus.helper._config()
        config.validate_isolation((self.operations_db, self.main_db, self.cache_db))
        platform = EvidencePlatform(config)
        platform.writer.primitive_engine = None
        recovery = WatchtowerShadowRecovery(
            operations_db=self.operations_db, main_db=self.main_db,
            transaction_cache_db=self.cache_db, output_root=self.output_root,
            limits=limits,
        )
        recovery.materializer = self.corpus.helper
        recovery.budget = RecoveryBudget(limits)
        results: dict[str, Any] = {}
        platform.writer.start()
        try:
            urls = recovery._rpc_urls()
            async with aiohttp.ClientSession() as session:
                client = SharedTransactionAcquisition(
                    session, semaphore=asyncio.Semaphore(2),
                    telemetry_sink=recovery.budget.observe,
                )
                for subject in plan["subjects"]:
                    creator = subject["creator"]
                    reference = subject["activation_reference"]
                    if self._reference_present(creator, reference):
                        results[creator] = {
                            "status": "ALREADY_RECOVERED", "pages": [],
                            "activation_reference": reference,
                        }
                        continue
                    cursor = subject["next_cursor"]
                    pages = []
                    outcome = "REFERENCE_NOT_FOUND_WITHIN_BOUND"
                    for page in range(2, MAX_ADDITIONAL_PAGES + 2):
                        payload = {
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [creator, {
                                "limit": 1000, "commitment": "finalized",
                                "before": cursor,
                            }],
                        }
                        response = await recovery._request(
                            client, platform, urls, payload,
                            purpose="creator_freshness_reference_completion",
                            creator=creator, launch=subject["mint"],
                            page=page, cursor=cursor,
                        )
                        if response is None:
                            outcome = "PROVIDER_UNAVAILABLE"
                            break
                        data = response.data if isinstance(response.data, Mapping) else {}
                        rows = data.get("result") if isinstance(data.get("result"), list) else []
                        signatures = [
                            row.get("signature") for row in rows
                            if isinstance(row, Mapping) and isinstance(row.get("signature"), str)
                        ]
                        pages.append({"page": page, "returned": len(signatures),
                                      "reference_found": reference in signatures})
                        if reference in signatures:
                            outcome = "REFERENCE_RECOVERED"
                            break
                        if not signatures:
                            outcome = "HISTORICAL_UNAVAILABLE"
                            break
                        cursor = signatures[-1]
                    results[creator] = {"status": outcome, "pages": pages,
                                        "activation_reference": reference}
            if not platform.mirror.drain(timeout=300):
                raise RuntimeError("Evidence mirror drain timeout")
            writer = self.corpus.helper._drain_writer(platform)
            health = platform.health()
        finally:
            platform.writer.stop()
            platform.mirror.stop()
        report = {
            "milestone": "EP3.2B", "authority": "SHADOW_NON_AUTHORITATIVE",
            "plan": plan, "results": results, "budget": recovery.budget.report(),
            "writer": writer, "health": health,
            "duration_seconds": round(time.monotonic() - started, 3),
            "production_writes": 0, "transaction_fetches": 0,
            "population_expansion": False, "primitive_engine_modified": False,
            "runtime_modified": False, "operation_contract_modified": False,
        }
        self.corpus.helper._write_json(self.output_root / "ep3_2b_recovery.json", report)
        return report
