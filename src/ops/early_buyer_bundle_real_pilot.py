"""Bounded real/injected execution shell for the frozen early-buyer pilot.

Provider payloads are deliberately kept only in call-local byte strings.  The
adapter remains transport-injected; this module is the sole place with optional
real HTTP behaviour.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .early_buyer_bundle_history import (
    OfflineStore, PILOT_DURABLE_MAX, PILOT_NETWORK_MAX, build_v2, digest,
    normalize_v2,
)
from .early_buyer_bundle_provider_adapter import acquire_one

RUNNER_VERSION = "EARLY_BUYER_BUNDLE_REAL_PILOT_RUNNER_V1"
BIRDEYE_URL = "https://public-api.birdeye.so/defi/v3/token/txs"
APPROVED_HELIUS_METHODS = frozenset(("getTransaction", "getBlock"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(raw)
    os.replace(tmp, path)
    if path.read_text() != raw:
        raise RuntimeError("CHECKPOINT_READBACK_FAILED")


class RealProviderTransport:
    """Explicit real network boundary.  Tests pass an injected opener instead."""
    def __init__(self, birdeye_key: str, helius_url: str, opener=urllib.request.urlopen,
                 sleeper=time.sleep, timeout: int = 30, status_hook=None):
        if not birdeye_key or not helius_url:
            raise RuntimeError("PROVIDER_CREDENTIAL_UNAVAILABLE")
        self.birdeye_key, self.helius_url = birdeye_key, helius_url
        self.opener, self.sleeper, self.timeout, self.status_hook = opener, sleeper, timeout, status_hook

    @classmethod
    def from_environment(cls, **kwargs):
        return cls(os.environ.get("BIRDEYE", ""), os.environ.get("HELIUS_RPC_URL", ""), **kwargs)

    def birdeye(self, request):
        params = urllib.parse.urlencode(request["params"])
        outbound = urllib.request.Request(
            BIRDEYE_URL + "?" + params,
            headers={"X-API-KEY": self.birdeye_key, "x-chain": "solana", "accept": "application/json"},
        )
        try:
            with self.opener(outbound, timeout=self.timeout) as response:
                status, body, headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, body, headers = error.code, error.read(), dict(error.headers or {})
        if self.status_hook:
            self.status_hook(status, len(body), headers)
        if status == 429:
            value = headers.get("Retry-After")
            try:
                delay = max(0, min(30, int(value)))
            except (TypeError, ValueError):
                delay = 1
            self.sleeper(delay)
        return status, body, headers

    def helius(self, method: str, params: list):
        if method not in APPROVED_HELIUS_METHODS:
            raise RuntimeError("UNPLANNED_PROVIDER_ACTION")
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        outbound = urllib.request.Request(self.helius_url, data=raw, headers={"Content-Type": "application/json"})
        with self.opener(outbound, timeout=self.timeout) as response:
            body = response.read()
            if self.status_hook:
                self.status_hook(response.status, len(body), dict(response.headers))
            return response.status, body, dict(response.headers)


class FrozenPilotRunner:
    def __init__(self, manifest: Path, run_dir: Path, birdeye_transport, *, expected_sha256: str | None = None,
                 after_response_hook=None):
        self.manifest, self.run_dir, self.birdeye_transport = Path(manifest), Path(run_dir), birdeye_transport
        self.after_response_hook = after_response_hook
        self.manifest_sha256 = file_sha256(self.manifest)
        if expected_sha256 and self.manifest_sha256 != expected_sha256:
            raise RuntimeError("MANIFEST_DIGEST_MISMATCH")
        self.population = json.loads(self.manifest.read_text()).get("selected_mints")
        if not isinstance(self.population, list) or len(self.population) != 12 or len(set(self.population)) != 12:
            raise RuntimeError("FROZEN_POPULATION_INVALID")
        self.checkpoint_path = self.run_dir / "checkpoint.json"
        self.store = OfflineStore(self.run_dir / "records")
        self.state = self._load_or_new()

    def _load_or_new(self):
        if not self.checkpoint_path.exists():
            return {"version": RUNNER_VERSION, "manifest_sha256": self.manifest_sha256, "current_index": 0,
                    "completed_mints": [], "terminal": {}, "mint_counters": {}, "provider_bytes": 0,
                    "hold_reason": None}
        state = json.loads(self.checkpoint_path.read_text())
        if state.get("version") != RUNNER_VERSION or state.get("manifest_sha256") != self.manifest_sha256:
            raise RuntimeError("CHECKPOINT_MANIFEST_MISMATCH")
        return state

    def _save(self):
        atomic_json(self.checkpoint_path, self.state)

    def _durable_bytes(self):
        return sum(p.stat().st_size for p in self.run_dir.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))

    def _account(self, mint, status, byte_count, headers):
        counters = self.state["mint_counters"].setdefault(mint, {})
        counters["birdeye_attempts"] = counters.get("birdeye_attempts", 0) + 1
        counters["birdeye_429s"] = counters.get("birdeye_429s", 0) + (status == 429)
        counters["birdeye_bytes"] = counters.get("birdeye_bytes", 0) + byte_count
        self.state["provider_bytes"] += byte_count
        if self.state["provider_bytes"] > PILOT_NETWORK_MAX:
            self.state["hold_reason"] = "FAIL_NETWORK_BUDGET"
            self._save()
            raise RuntimeError("FAIL_NETWORK_BUDGET")
        self._save()

    def run(self, contexts: dict[str, dict], *, interrupt_after: int | None = None):
        for index, mint in enumerate(self.population):
            if mint in self.state["completed_mints"]:
                continue
            if mint not in contexts:
                raise RuntimeError("FROZEN_CONTEXT_MISSING")
            self.state["current_index"] = index
            self.state["current_mint"] = mint
            self._save()
            counters = self.state["mint_counters"].setdefault(mint, {})
            def transport(request):
                status, body, headers = self.birdeye_transport(request)
                self._account(mint, status, len(body), headers)
                if self.after_response_hook:
                    self.after_response_hook(status, mint)
                return status, body, headers
            context = contexts[mint]
            acquired = acquire_one(transport, mint, context["launch"]["block_time"], context["launch"]["block_time"] + 60, counters)
            normalized = normalize_v2(acquired["trades"], context["creator"], context["launch"]["block_time"], mint=mint, provider_mint=acquired.get("provider_mint"))
            record = build_v2(context, normalized)
            if record["first_actionable_buy"] is not None:
                raise RuntimeError("ACTIONABLE_ENTRY_FORBIDDEN")
            logical = self.store.commit(record)
            if digest(self.store.read(mint)) != digest(json.loads(json.dumps(record))):
                raise RuntimeError("READBACK_FAILED")
            if self._durable_bytes() > PILOT_DURABLE_MAX:
                raise RuntimeError("FAIL_STORAGE_BUDGET")
            self.state["completed_mints"].append(mint)
            self.state["terminal"][mint] = {"status": "TERMINAL", "logical_id": logical, "canonical_bytes": len(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())}
            self.state["current_mint"] = None
            self.state["current_index"] = index + 1
            self._save()
            if interrupt_after is not None and len(self.state["completed_mints"]) == interrupt_after:
                raise RuntimeError("INJECTED_INTERRUPTION")
        self.state["terminal_status"] = "COMPLETE"
        self._save()
        return self.state
