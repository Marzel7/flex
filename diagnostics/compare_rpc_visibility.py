#!/usr/bin/env python3
"""Compare Solana RPC visibility between Helius and Alchemy.

Read-only diagnostic harness. It does not import WATCHTOWER modules, open local
databases, write files, or change services.

Examples:
  python diagnostics/compare_rpc_visibility.py \
    --tx funding:3NbKD9G4Ufxhn5wDnz7TFGWmsuBw27quk3f2nUzVXqm9swzVJ2uwtv5LPJ9bdEsKwte1oxCgnVzsWzBbGKHeheKs \
    --tx create:2dbamNo3GYGqzHtWVBMgmqwpoSimey4MPVh5AEkBwu91ZSNJy8w1Ve3sVEViAnencDtqnX5tqDBSf6Z38TtMJdAa \
    --creator EpgUTPSS:4xej7jryB7nbUZkCx5DcszsgHuD3GJwUFrJrMmQFNSza

  python diagnostics/compare_rpc_visibility.py \
    --poll 3NbKD9G4Ufxhn5wDnz7TFGWmsuBw27quk3f2nUzVXqm9swzVJ2uwtv5LPJ9bdEsKwte1oxCgnVzsWzBbGKHeheKs \
    --poll-timeout-ms 8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader for KEY=VALUE and export KEY=VALUE lines."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def _mask_url(url: str) -> str:
    if not url:
        return ""
    if "api-key=" in url:
        head, tail = url.split("api-key=", 1)
        return head + "api-key=" + tail[:6] + "..."
    if "/v2/" in url:
        head, tail = url.split("/v2/", 1)
        return head + "/v2/" + tail[:6] + "..."
    return url


def _resolve_helius_url() -> str:
    url = os.environ.get("HELIUS_RPC_URL") or os.environ.get("RPC_URL")
    if url:
        return url
    key = os.environ.get("HELIUS_API_KEY")
    if key:
        return f"https://mainnet.helius-rpc.com/?api-key={key}"
    raise SystemExit("Missing Helius config: set HELIUS_RPC_URL or HELIUS_API_KEY")


def _resolve_alchemy_url() -> str:
    for key in (
        "ALCHEMY_RPC_URL",
        "ALCHEMY_SOLANA_RPC_URL",
        "SOLANA_ALCHEMY_RPC_URL",
        "ALCHEMY_SOLANA_MAINNET_RPC_URL",
    ):
        url = os.environ.get(key)
        if url:
            return url
    key = os.environ.get("ALCHEMY_API_KEY") or os.environ.get("ALCHEMY_SOLANA_API_KEY")
    if key:
        return f"https://solana-mainnet.g.alchemy.com/v2/{key}"
    raise SystemExit(
        "Missing Alchemy config: set ALCHEMY_RPC_URL or ALCHEMY_API_KEY "
        "(for example: export ALCHEMY_API_KEY=...)"
    )


@dataclass
class RpcResult:
    ok: bool
    result: Any
    latency_ms: int
    response_bytes: int
    error: str | None = None


def rpc(url: str, method: str, params: list[Any], timeout_s: float) -> RpcResult:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = json.loads(raw.decode("utf-8"))
        if data.get("error"):
            return RpcResult(False, None, latency_ms, len(raw), str(data.get("error")))
        return RpcResult(True, data.get("result"), latency_ms, len(raw))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return RpcResult(False, None, latency_ms, 0, repr(exc))


def parse_label_value(value: str, default_label: str) -> tuple[str, str]:
    if ":" in value:
        label, raw = value.split(":", 1)
        return label.strip() or default_label, raw.strip()
    return default_label, value.strip()


def table(headers: list[str], rows: list[list[Any]]) -> str:
    rendered = [[str(x) if x is not None else "-" for x in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rendered:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    out = [fmt.format(*headers), fmt.format(*["-" * w for w in widths])]
    out.extend(fmt.format(*row) for row in rendered)
    return "\n".join(out)


def tx_summary(provider: str, label: str, sig: str, res: RpcResult) -> list[Any]:
    tx = res.result if isinstance(res.result, dict) else None
    return [
        label,
        provider,
        "yes" if tx else "no",
        res.latency_ms,
        res.response_bytes,
        tx.get("slot") if tx else None,
        tx.get("blockTime") if tx else None,
        sig[:12] + "...",
        res.error or "",
    ]


def sig_summary(provider: str, label: str, wallet: str, res: RpcResult) -> list[Any]:
    sigs = res.result if isinstance(res.result, list) else []
    newest = sigs[0].get("signature") if sigs and isinstance(sigs[0], dict) else None
    newest_slot = sigs[0].get("slot") if sigs and isinstance(sigs[0], dict) else None
    newest_bt = sigs[0].get("blockTime") if sigs and isinstance(sigs[0], dict) else None
    return [
        label,
        provider,
        len(sigs),
        res.latency_ms,
        newest[:12] + "..." if newest else "-",
        newest_slot,
        newest_bt,
        wallet[:12] + "...",
        res.error or "",
    ]


def run_get_transaction(providers: dict[str, str], txs: list[tuple[str, str]], timeout_s: float) -> None:
    if not txs:
        return
    rows = []
    for label, sig in txs:
        for provider, url in providers.items():
            res = rpc(
                url,
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
                timeout_s,
            )
            rows.append(tx_summary(provider, label, sig, res))
    print("\ngetTransaction comparison")
    print("-------------------------")
    print(table(["case", "provider", "found", "ms", "bytes", "slot", "blockTime", "signature", "error"], rows))


def run_get_signatures(providers: dict[str, str], creators: list[tuple[str, str]], limit: int,
                       commitment: str, timeout_s: float) -> None:
    if not creators:
        return
    rows = []
    for label, wallet in creators:
        for provider, url in providers.items():
            res = rpc(
                url,
                "getSignaturesForAddress",
                [wallet, {"limit": limit, "commitment": commitment}],
                timeout_s,
            )
            rows.append(sig_summary(provider, label, wallet, res))
    print("\ngetSignaturesForAddress comparison")
    print("----------------------------------")
    print(table(["case", "provider", "count", "ms", "newest", "slot", "blockTime", "wallet", "error"], rows))


def run_poll(providers: dict[str, str], sig: str, timeout_ms: int, interval_ms: int, timeout_s: float) -> None:
    if not sig:
        return
    print("\ngetTransaction availability polling")
    print("-----------------------------------")
    started = time.perf_counter()
    attempts = {name: 0 for name in providers}
    winners: dict[str, dict[str, Any]] = {}
    while int((time.perf_counter() - started) * 1000) <= timeout_ms and len(winners) < len(providers):
        for provider, url in providers.items():
            if provider in winners:
                continue
            attempts[provider] += 1
            res = rpc(
                url,
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
                timeout_s,
            )
            if isinstance(res.result, dict):
                winners[provider] = {
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempts[provider],
                    "slot": res.result.get("slot"),
                    "blockTime": res.result.get("blockTime"),
                    "last_latency_ms": res.latency_ms,
                }
        if len(winners) < len(providers):
            time.sleep(max(0, interval_ms) / 1000.0)
    rows = []
    for provider in providers:
        info = winners.get(provider)
        rows.append([
            provider,
            "yes" if info else "no",
            info.get("elapsed_ms") if info else "-",
            attempts[provider],
            info.get("last_latency_ms") if info else "-",
            info.get("slot") if info else "-",
            info.get("blockTime") if info else "-",
        ])
    print(table(["provider", "available", "elapsed_ms", "attempts", "last_rpc_ms", "slot", "blockTime"], rows))
    if winners:
        first = sorted(winners.items(), key=lambda kv: kv[1]["elapsed_ms"])[0]
        print(f"\nWinner: {first[0]} ({first[1]['elapsed_ms']} ms, attempts={first[1]['attempts']})")
    else:
        print("\nWinner: none within timeout")


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Read-only Helius vs Alchemy Solana RPC visibility comparison")
    parser.add_argument("--tx", action="append", default=[], help="Transaction signature, optionally label:sig")
    parser.add_argument("--creator", action="append", default=[], help="Creator wallet, optionally label:wallet")
    parser.add_argument("--poll", default="", help="Optional signature to poll with getTransaction")
    parser.add_argument("--poll-timeout-ms", type=int, default=8000)
    parser.add_argument("--poll-interval-ms", type=int, default=250)
    parser.add_argument("--sig-limit", type=int, default=5)
    parser.add_argument("--sig-commitment", default="confirmed",
                        choices=("confirmed", "finalized", "processed"),
                        help="Commitment for getSignaturesForAddress; providers commonly reject processed")
    parser.add_argument("--timeout-s", type=float, default=10.0)
    args = parser.parse_args()

    providers = {
        "Helius": _resolve_helius_url(),
        "Alchemy": _resolve_alchemy_url(),
    }
    print("Providers")
    print("---------")
    for name, url in providers.items():
        print(f"{name}: {_mask_url(url)}")

    txs = [parse_label_value(x, f"tx{i+1}") for i, x in enumerate(args.tx)]
    creators = [parse_label_value(x, f"creator{i+1}") for i, x in enumerate(args.creator)]

    run_get_transaction(providers, txs, args.timeout_s)
    run_get_signatures(providers, creators, args.sig_limit, args.sig_commitment, args.timeout_s)
    run_poll(providers, args.poll.strip(), args.poll_timeout_ms, args.poll_interval_ms, args.timeout_s)

    if not txs and not creators and not args.poll:
        print("\nNo tests requested. Add --tx, --creator, or --poll.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
