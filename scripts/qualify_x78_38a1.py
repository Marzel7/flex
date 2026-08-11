#!/usr/bin/env python3
"""Bounded shadow probe for Helius enhanced-history overlap semantics.

Maximum three creators and three requests per creator.  It deliberately uses
the same Shared Transaction Acquisition transport as creator funding and never
writes the production database.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path

import aiohttp

from src.acquisition.factory import build_transaction_acquisition
from src.acquisition.transaction import acquisition_scope
from src.core.creator_history_overlap import continuation_request, verify_overlap


CREATORS = (
    "bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa",
    "Gygj9QQby4j2jryqyqBHvLP7ctv2SaANgh4sCb69BUpA",
    "3ct4z3q2XHEGTb4Noe3Wh5iySA8e5UZawUXkWAnkfh5J",
)
OUT = Path("docs/audits/x78_38a1_provider_artifacts")
REPORT = Path("docs/audits/x78_38a1_provider_experiment.json")


def request_url(creator: str, before: str | None = None) -> str:
    key = os.environ["HELIUS_API_KEY"]
    url = (
        f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"
        f"?api-key={key}&limit=100&sort-order=desc&commitment=finalized"
    )
    return f"{url}&before={before}" if before else url


def persist(raw: bytes | None) -> dict:
    body = raw or b""
    digest = hashlib.sha256(body).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{digest}.json.gz"
    if not path.exists():
        with gzip.open(path, "wb") as handle:
            handle.write(body)
    return {"sha256": digest, "path": str(path), "bytes": len(body)}


async def fetch(client, creator: str, page: int, before: str | None):
    with acquisition_scope(purpose="x78_38a1_shadow_pagination", creator=creator):
        response = await client.request_once(
            http_method="GET", url=request_url(creator, before), timeout_seconds=30,
            request_type="enhanced_address_page", method="helius_enhanced_addresses_transactions",
            page_number=page, cursor=before, cache_state="shadow_no_cache",
        )
    page_data = response.data if isinstance(response.data, list) else []
    return response, page_data


async def main() -> None:
    if not os.environ.get("HELIUS_API_KEY"):
        raise SystemExit("HELIUS_API_KEY is required")
    rows = []
    semaphore = asyncio.Semaphore(8)
    timeout = aiohttp.ClientTimeout(total=35)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        client = build_transaction_acquisition(session, semaphore=semaphore)
        for creator in CREATORS:
            first_response, first = await fetch(client, creator, 1, None)
            first_artifact = persist(first_response.raw_body)
            row = {
                "creator": creator,
                "first": {"status": first_response.status, "count": len(first), "artifact": first_artifact},
            }
            if first_response.error or first_response.status != 200 or len(first) < 2:
                row["classification"] = "PROVIDER_ERROR" if first_response.error else "INSUFFICIENT_PAGE_FOR_OVERLAP"
                rows.append(row)
                continue
            ordinary_before = str(first[-1].get("signature") or "")
            ordinary_response, ordinary = await fetch(client, creator, 2, ordinary_before)
            row["ordinary"] = {"status": ordinary_response.status, "count": len(ordinary), "artifact": persist(ordinary_response.raw_body)}
            protocol = continuation_request(first)
            if protocol is None:
                row["classification"] = "INSUFFICIENT_PAGE_FOR_OVERLAP"
                rows.append(row)
                continue
            overlap_response, overlap = await fetch(client, creator, 2, protocol.before_signature)
            row["overlap"] = {"status": overlap_response.status, "count": len(overlap), "artifact": persist(overlap_response.raw_body)}
            verdict = verify_overlap(protocol, overlap)
            row["protocol"] = {
                "before_signature": protocol.before_signature,
                "expected_overlap_signature": protocol.expected_overlap_signature,
                "expected_overlap_slot": protocol.expected_overlap_slot,
                "verdict": verdict.__dict__,
            }
            row["classification"] = "CONTIGUOUS_PROVEN" if verdict.contiguous else (
                "EXHAUSTED" if verdict.reason.startswith("provider_exhaustion") else verdict.reason.upper()
            )
            rows.append(row)
    report = {
        "milestone": "X78.38A1", "mode": "shadow_only", "request_budget": 9,
        "requests_issued": sum(1 + (1 if "ordinary" in row else 0) + (1 if "overlap" in row else 0) for row in rows),
        "provider": "helius_enhanced_addresses_transactions", "rows": rows,
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"requests_issued": report["requests_issued"], "classifications": [r["classification"] for r in rows]}))


if __name__ == "__main__":
    asyncio.run(main())
