"""Quote-only Jupiter paper trading simulation.

This module deliberately does not load keypairs, sign transactions, or submit
swaps. It records simulated entries/exits using Jupiter quote responses so the
app can evaluate buy/sell ideas without touching funds.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import requests


WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_TOKEN_DECIMALS = 6
LAMPORTS_PER_SOL = 1_000_000_000
DEFAULT_PUMPFUN_SUPPLY_UI = 1_000_000_000
DEFAULT_SLIPPAGE_BPS = 500
STRATEGY_CASCADE_TARGETS = (1.5, 2.5, 3.5, 5.0, 7.0, 10.0)
STRATEGY_STOP_RULES = (
    {"ratio": 0.5, "window_seconds": 30 * 60, "label": "50%/30m"},
    {"ratio": 0.3, "window_seconds": 60 * 60, "label": "30%/60m"},
)
STRATEGY_TARGETS = {
    "target_1_5": 1.5,
    "target_2_5": 2.5,
    "target_3": 3.0,
    "target_3_5": 3.5,
    "target_5": 5.0,
    "target_7": 7.0,
    "target_10": 10.0,
}
STRATEGY_NAMES = {
    "current": "Current",
    "peak": "Peak",
    "cascade": "Cascade",
    **{key: f"{value:g}x" for key, value in STRATEGY_TARGETS.items()},
}
JUPITER_QUOTE_URL = os.environ.get(
    "JUPITER_QUOTE_URL",
    "https://api.jup.ag/swap/v1/quote",
)
JUPITER_SWAP_URL = os.environ.get(
    "JUPITER_SWAP_URL",
    "https://api.jup.ag/swap/v1/swap",
)
TRADING_SIM_DRY_RUN_ENABLED = os.environ.get(
    "TRADING_SIM_DRY_RUN_ENABLED",
    "1",
).strip().lower() not in {"0", "false", "no", "off"}
TRADING_SIM_DRY_RUN_PUBLIC_KEY = (
    os.environ.get("TRADING_SIM_DRY_RUN_PUBLIC_KEY")
    or os.environ.get("TRADING_SIM_PUBLIC_KEY")
    or os.environ.get("WALLET_PUBLIC_KEY")
    or ""
).strip()
TRADING_SIM_DRY_RUN_RPC_URL = (
    os.environ.get("TRADING_SIM_DRY_RUN_RPC_URL")
    or os.environ.get("RPC_HTTP")
    or os.environ.get("RPC_URL")
    or os.environ.get("HELIUS_RPC_URL")
    or "https://api.mainnet-beta.solana.com"
)


class TradingSimulationError(RuntimeError):
    """Raised when a simulation request cannot be completed."""


def _now() -> int:
    return int(time.time())


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _quote_swap_usd_value(quote: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(quote, dict):
        return None
    raw = quote.get("raw")
    if not isinstance(raw, dict):
        return None
    return _safe_float(raw.get("swapUsdValue"))


def _normalize_strategy(strategy: Optional[str]) -> str:
    key = str(strategy or "current").strip().lower()
    return key if key in {"current", "peak", "cascade", *STRATEGY_TARGETS.keys()} else "current"


def _route_labels(route_plan: Iterable[Dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for step in route_plan or []:
        swap_info = step.get("swapInfo") or {}
        label = swap_info.get("label") or swap_info.get("ammKey")
        if label and label not in labels:
            labels.append(str(label))
    return labels


def _compact_swap_build(build_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "last_valid_block_height": build_data.get("lastValidBlockHeight"),
        "prioritization_fee_lamports": build_data.get("prioritizationFeeLamports"),
        "compute_unit_limit": build_data.get("computeUnitLimit"),
        "prioritization_type": build_data.get("prioritizationType"),
        "dynamic_slippage_report": build_data.get("dynamicSlippageReport"),
    }


def _token_balance_amount(balance: Dict[str, Any]) -> Optional[int]:
    amount = ((balance.get("uiTokenAmount") or {}).get("amount"))
    return _safe_int(amount)


def _simulated_token_delta(value: Dict[str, Any], mint: str, owner: str) -> Optional[int]:
    if not value or not mint:
        return None
    pre: Dict[int, int] = {}
    for balance in value.get("preTokenBalances") or []:
        if balance.get("mint") == mint and (not owner or balance.get("owner") == owner):
            account_index = _safe_int(balance.get("accountIndex"))
            amount = _token_balance_amount(balance)
            if account_index is not None and amount is not None:
                pre[account_index] = amount
    deltas: list[int] = []
    for balance in value.get("postTokenBalances") or []:
        if balance.get("mint") == mint and (not owner or balance.get("owner") == owner):
            account_index = _safe_int(balance.get("accountIndex"))
            amount = _token_balance_amount(balance)
            if account_index is not None and amount is not None:
                deltas.append(amount - pre.get(account_index, 0))
    if not deltas:
        return None
    positive = [delta for delta in deltas if delta > 0]
    return max(positive) if positive else max(deltas)


def _dry_run_error_text(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("InstructionError"), list):
        parts = value["InstructionError"]
        index = parts[0] if parts else "?"
        detail = parts[1] if len(parts) > 1 else None
        if isinstance(detail, dict) and "Custom" in detail:
            return f"ix {index} custom {detail['Custom']}"
        return f"ix {index} {detail}"
    try:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    except Exception:
        return str(value)


@dataclass
class QuoteResult:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    slippage_bps: int
    price_impact_pct: Optional[float]
    route_labels: list[str]
    raw: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_mint": self.input_mint,
            "output_mint": self.output_mint,
            "in_amount": self.in_amount,
            "out_amount": self.out_amount,
            "slippage_bps": self.slippage_bps,
            "price_impact_pct": self.price_impact_pct,
            "route_labels": self.route_labels,
            "raw": self.raw,
        }


class JupiterQuoteClient:
    """Small quote-only Jupiter client."""

    def __init__(
        self,
        quote_url: str = JUPITER_QUOTE_URL,
        swap_url: str = JUPITER_SWAP_URL,
        rpc_url: str = TRADING_SIM_DRY_RUN_RPC_URL,
        timeout: int = 10,
    ):
        self.quote_url = quote_url
        self.swap_url = swap_url
        self.rpc_url = rpc_url
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        api_key = os.environ.get("JUPITER_API_KEY")
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ) -> QuoteResult:
        if not input_mint or not output_mint:
            raise TradingSimulationError("input_mint and output_mint are required")
        if amount <= 0:
            raise TradingSimulationError("amount must be greater than zero")

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount),
            "slippageBps": int(slippage_bps),
        }
        headers = self._headers()
        try:
            response = requests.get(self.quote_url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise TradingSimulationError(f"Jupiter quote failed: {exc}") from exc
        except ValueError as exc:
            raise TradingSimulationError("Jupiter quote returned invalid JSON") from exc

        out_amount = _safe_int(data.get("outAmount"), 0) or 0
        if out_amount <= 0:
            message = data.get("error") or data.get("message") or "no route returned"
            raise TradingSimulationError(f"Jupiter quote unavailable: {message}")

        return QuoteResult(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=_safe_int(data.get("inAmount"), amount) or amount,
            out_amount=out_amount,
            slippage_bps=int(slippage_bps),
            price_impact_pct=_safe_float(data.get("priceImpactPct")),
            route_labels=_route_labels(data.get("routePlan") or []),
            raw=data,
        )

    def dry_run_buy(
        self,
        quote_response: Dict[str, Any],
        output_mint: str,
        user_public_key: str,
    ) -> Dict[str, Any]:
        """Build an unsigned Jupiter swap and simulate it without broadcasting."""
        if not user_public_key:
            return {"status": "not_configured", "reason": "TRADING_SIM_DRY_RUN_PUBLIC_KEY missing"}
        if not self.rpc_url:
            return {"status": "not_configured", "reason": "RPC URL missing"}

        build_payload = {
            "userPublicKey": user_public_key,
            "quoteResponse": quote_response,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": False,
            "dynamicSlippage": False,
            "skipUserAccountsRpcCalls": True,
        }
        started = time.time()
        try:
            build_response = requests.post(
                self.swap_url,
                json=build_payload,
                headers={"Content-Type": "application/json", **self._headers()},
                timeout=self.timeout,
            )
            build_response.raise_for_status()
            build_data = build_response.json()
        except requests.RequestException as exc:
            return {"status": "build_failed", "error": str(exc), "latency_ms": int((time.time() - started) * 1000)}
        except ValueError as exc:
            return {"status": "build_failed", "error": "Jupiter swap returned invalid JSON", "latency_ms": int((time.time() - started) * 1000)}

        swap_tx = build_data.get("swapTransaction")
        if not swap_tx:
            return {
                "status": "build_failed",
                "error": build_data.get("error") or build_data.get("message") or "missing swapTransaction",
                "latency_ms": int((time.time() - started) * 1000),
            }

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "simulateTransaction",
            "params": [
                swap_tx,
                {
                    "encoding": "base64",
                    "commitment": "processed",
                    "sigVerify": False,
                    "replaceRecentBlockhash": True,
                    "innerInstructions": False,
                },
            ],
        }
        try:
            rpc_response = requests.post(self.rpc_url, json=rpc_payload, timeout=self.timeout)
            rpc_response.raise_for_status()
            rpc_data = rpc_response.json()
        except requests.RequestException as exc:
            return {
                "status": "simulate_failed",
                "error": str(exc),
                "build": _compact_swap_build(build_data),
                "latency_ms": int((time.time() - started) * 1000),
            }
        except ValueError:
            return {
                "status": "simulate_failed",
                "error": "RPC returned invalid JSON",
                "build": _compact_swap_build(build_data),
                "latency_ms": int((time.time() - started) * 1000),
            }

        rpc_error = rpc_data.get("error")
        value = ((rpc_data.get("result") or {}).get("value") or {}) if not rpc_error else {}
        sim_error = value.get("err") if value else None
        error_value = rpc_error or sim_error
        quoted_out = _safe_int(quote_response.get("outAmount"), 0) or 0
        simulated_out = _simulated_token_delta(value, output_mint, user_public_key)
        diff_pct = (
            ((simulated_out - quoted_out) / quoted_out * 100.0)
            if quoted_out and simulated_out is not None
            else None
        )
        return {
            "status": "ok" if not rpc_error and sim_error is None else "failed",
            "error": error_value,
            "error_text": _dry_run_error_text(error_value),
            "fee_lamports": value.get("fee"),
            "units_consumed": value.get("unitsConsumed"),
            "quoted_out_amount": quoted_out,
            "simulated_out_amount": simulated_out,
            "simulated_out_diff_pct": diff_pct,
            "build": _compact_swap_build(build_data),
            "rpc_calls": 1,
            "latency_ms": int((time.time() - started) * 1000),
        }


class TradingSimulationService:
    """Stores paper buys and sells from Jupiter quote data."""

    def __init__(self, quote_client: Optional[JupiterQuoteClient] = None):
        self.quote_client = quote_client or JupiterQuoteClient()

    def ensure_schema(self, conn) -> None:
        existing = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                  AND name IN ('trade_simulations', 'trade_simulation_events')
                """
            ).fetchall()
        }
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_simulations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                token_symbol TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                entry_sol REAL NOT NULL,
                entry_lamports INTEGER NOT NULL,
                token_amount_raw INTEGER NOT NULL,
                token_amount_ui REAL,
                entry_price_sol REAL,
                entry_market_cap REAL,
                entry_market_cap_source TEXT,
                slippage_bps INTEGER NOT NULL DEFAULT 500,
                entry_quote_json TEXT,
                opened_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                closed_at INTEGER,
                exit_sol REAL,
                exit_lamports INTEGER,
                exit_price_sol REAL,
                exit_quote_json TEXT,
                pnl_sol REAL,
                pnl_pct REAL,
                notes TEXT
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(trade_simulations)").fetchall()
        }
        if "entry_market_cap" not in columns:
            conn.execute("ALTER TABLE trade_simulations ADD COLUMN entry_market_cap REAL")
        if "entry_market_cap_source" not in columns:
            conn.execute("ALTER TABLE trade_simulations ADD COLUMN entry_market_cap_source TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_simulation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                simulation_id INTEGER,
                mint TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                FOREIGN KEY(simulation_id) REFERENCES trade_simulations(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_simulation_claims (
                mint TEXT PRIMARY KEY,
                simulation_id INTEGER,
                claim_type TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                completed_at INTEGER,
                FOREIGN KEY(simulation_id) REFERENCES trade_simulations(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_simulations_status_opened ON trade_simulations(status, opened_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_simulations_mint ON trade_simulations(mint)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS liq_caught (
                mint TEXT PRIMARY KEY,
                token_symbol TEXT,
                network_name TEXT NOT NULL,
                liq_rate REAL NOT NULL,
                caught_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                confirmed_liq INTEGER NOT NULL DEFAULT 0,
                confirmed_at INTEGER,
                peak_market_cap REAL,
                creator_address TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_liq_caught_caught_at ON liq_caught(caught_at DESC)"
        )
        conn.commit()

    def quote_buy(
        self,
        mint: str,
        sol_amount: float,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ) -> Dict[str, Any]:
        if not mint:
            raise TradingSimulationError("mint is required")
        if float(sol_amount) <= 0:
            raise TradingSimulationError("SOL amount must be greater than zero")
        lamports = int(float(sol_amount) * LAMPORTS_PER_SOL)
        quote = self.quote_client.get_quote(
            WRAPPED_SOL_MINT,
            mint,
            lamports,
            slippage_bps,
        )
        token_amount_ui = quote.out_amount / (10 ** DEFAULT_TOKEN_DECIMALS)
        entry_price_sol = float(sol_amount) / token_amount_ui if token_amount_ui else None
        swap_usd_value = _safe_float(quote.raw.get("swapUsdValue"))
        implied_market_cap = (
            (swap_usd_value / token_amount_ui * DEFAULT_PUMPFUN_SUPPLY_UI)
            if swap_usd_value and token_amount_ui
            else None
        )
        payload = quote.to_dict()
        payload.update(
            {
                "side": "BUY",
                "sol_amount": float(sol_amount),
                "token_amount_raw": quote.out_amount,
                "token_amount_ui": token_amount_ui,
                "entry_price_sol": entry_price_sol,
                "entry_market_cap": implied_market_cap,
            }
        )
        if TRADING_SIM_DRY_RUN_ENABLED:
            try:
                payload["dry_run"] = self.quote_client.dry_run_buy(
                    quote.raw,
                    mint,
                    TRADING_SIM_DRY_RUN_PUBLIC_KEY,
                )
                payload["dry_run"]["entry_market_cap"] = implied_market_cap
            except Exception as exc:
                payload["dry_run"] = {"status": "error", "error": str(exc), "entry_market_cap": implied_market_cap}
        else:
            payload["dry_run"] = {"status": "disabled", "entry_market_cap": implied_market_cap}
        return payload

    def quote_sell(
        self,
        mint: str,
        token_amount_raw: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ) -> Dict[str, Any]:
        if not mint:
            raise TradingSimulationError("mint is required")
        if int(token_amount_raw) <= 0:
            raise TradingSimulationError("token amount must be greater than zero")
        quote = self.quote_client.get_quote(
            mint,
            WRAPPED_SOL_MINT,
            int(token_amount_raw),
            slippage_bps,
        )
        exit_sol = quote.out_amount / LAMPORTS_PER_SOL
        token_amount_ui = int(token_amount_raw) / (10 ** DEFAULT_TOKEN_DECIMALS)
        exit_price_sol = exit_sol / token_amount_ui if token_amount_ui else None
        payload = quote.to_dict()
        payload.update(
            {
                "side": "SELL",
                "exit_sol": exit_sol,
                "token_amount_raw": int(token_amount_raw),
                "token_amount_ui": token_amount_ui,
                "exit_price_sol": exit_price_sol,
            }
        )
        return payload

    def simulate_buy(
        self,
        conn,
        mint: str,
        sol_amount: float,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
        token_symbol: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema(conn)
        auto_claimed = False
        auto_buy = "auto_" in (notes or "")
        if auto_buy:
            existing = self.get_simulation_for_mint(conn, mint)
            if existing:
                return existing
            now = _now()
            conn.execute(
                """
                DELETE FROM trade_simulation_claims
                WHERE mint = ?
                  AND simulation_id IS NULL
                  AND created_at < ?
                """,
                (mint, now - 300),
            )
            claim_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO trade_simulation_claims (mint, claim_type, created_at)
                VALUES (?, 'AUTO_BUY', ?)
                """,
                (mint, now),
            )
            conn.commit()
            if claim_cursor.rowcount == 0:
                existing = self.get_simulation_for_mint(conn, mint)
                if existing:
                    return existing
                raise TradingSimulationError("auto buy already claimed for this mint")
            auto_claimed = True

        market_cap_row = conn.execute(
            """
            SELECT market_cap_current
            FROM token_analysis
            WHERE mint = ?
            """,
            (mint,),
        ).fetchone()
        entry_market_cap = None
        if market_cap_row:
            entry_market_cap = _safe_float(market_cap_row["market_cap_current"])
        try:
            quote = self.quote_buy(mint, sol_amount, slippage_bps)
        except Exception:
            if auto_claimed:
                conn.execute(
                    "DELETE FROM trade_simulation_claims WHERE mint = ? AND simulation_id IS NULL",
                    (mint,),
                )
                conn.commit()
            raise
        if quote.get("entry_market_cap"):
            entry_market_cap = quote["entry_market_cap"]
            entry_market_cap_source = "jupiter_quote.swapUsdValue"
        else:
            entry_market_cap_source = (
                "token_analysis.market_cap_current" if entry_market_cap is not None else None
            )
        cursor = conn.execute(
            """
            INSERT INTO trade_simulations (
                mint, token_symbol, status, entry_sol, entry_lamports,
                token_amount_raw, token_amount_ui, entry_price_sol,
                entry_market_cap, entry_market_cap_source,
                slippage_bps, entry_quote_json, opened_at, notes
            ) VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mint,
                token_symbol,
                float(sol_amount),
                int(float(sol_amount) * LAMPORTS_PER_SOL),
                int(quote["token_amount_raw"]),
                quote["token_amount_ui"],
                quote["entry_price_sol"],
                entry_market_cap,
                entry_market_cap_source,
                int(slippage_bps),
                _json_dumps(quote),
                _now(),
                notes,
            ),
        )
        simulation_id = cursor.lastrowid
        if auto_claimed:
            conn.execute(
                """
                UPDATE trade_simulation_claims
                SET simulation_id = ?, completed_at = ?
                WHERE mint = ?
                """,
                (simulation_id, _now(), mint),
            )
        conn.execute(
            """
            INSERT INTO trade_simulation_events (simulation_id, mint, event_type, payload_json, created_at)
            VALUES (?, ?, 'SIM_BUY', ?, ?)
            """,
            (simulation_id, mint, _json_dumps(quote), _now()),
        )
        conn.commit()
        return self.get_simulation(conn, simulation_id) or {}

    def simulate_sell(
        self,
        conn,
        simulation_id: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ) -> Dict[str, Any]:
        self.ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM trade_simulations WHERE id = ?",
            (int(simulation_id),),
        ).fetchone()
        if not row:
            raise TradingSimulationError("simulation not found")
        row_dict = dict(row)
        if row_dict.get("status") != "OPEN":
            raise TradingSimulationError("simulation is already closed")

        quote = self.quote_sell(row_dict["mint"], int(row_dict["token_amount_raw"]), slippage_bps)
        exit_sol = float(quote["exit_sol"])
        entry_sol = float(row_dict["entry_sol"])
        pnl_sol = exit_sol - entry_sol
        pnl_pct = (pnl_sol / entry_sol * 100.0) if entry_sol else None
        closed_at = _now()

        conn.execute(
            """
            UPDATE trade_simulations
            SET status='CLOSED',
                closed_at=?,
                exit_sol=?,
                exit_lamports=?,
                exit_price_sol=?,
                exit_quote_json=?,
                pnl_sol=?,
                pnl_pct=?
            WHERE id=?
            """,
            (
                closed_at,
                exit_sol,
                int(quote["out_amount"]),
                quote["exit_price_sol"],
                _json_dumps(quote),
                pnl_sol,
                pnl_pct,
                int(simulation_id),
            ),
        )
        conn.execute(
            """
            INSERT INTO trade_simulation_events (simulation_id, mint, event_type, payload_json, created_at)
            VALUES (?, ?, 'SIM_SELL', ?, ?)
            """,
            (int(simulation_id), row_dict["mint"], _json_dumps(quote), closed_at),
        )
        conn.commit()
        return self.get_simulation(conn, simulation_id) or {}

    def has_simulation_for_mint(self, conn, mint: str) -> bool:
        self.ensure_schema(conn)
        row = conn.execute(
            "SELECT 1 FROM trade_simulations WHERE mint = ? LIMIT 1",
            (mint,),
        ).fetchone()
        return row is not None

    def get_simulation_for_mint(self, conn, mint: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema(conn)
        row = conn.execute(
            "SELECT id FROM trade_simulations WHERE mint = ? ORDER BY opened_at ASC, id ASC LIMIT 1",
            (mint,),
        ).fetchone()
        if not row:
            return None
        return self.get_simulation(conn, int(row["id"]))

    def list_simulations(
        self,
        conn,
        limit: int = 100,
        status: Optional[str] = None,
        risk_level: Optional[str] = None,
        opened_since: Optional[int] = None,
        opened_before: Optional[int] = None,
        strategy: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        self.ensure_schema(conn)
        limit = max(1, min(int(limit or 100), 250))
        params: list[Any] = []
        clauses: list[str] = []
        if status and status.upper() in {"OPEN", "CLOSED"}:
            clauses.append("ts.status = ?")
            params.append(status.upper())
        if risk_level:
            if risk_level.upper() == 'LIQ':
                clauses.append("EXISTS (SELECT 1 FROM token_pool_accounts tpa2 WHERE tpa2.mint = ts.mint AND tpa2.liquidity_removed = 1)")
            else:
                clauses.append("COALESCE(tps.risk_level, 'UNKNOWN') = ?")
                params.append(risk_level.upper())
        if opened_since:
            clauses.append("ts.opened_at >= ?")
            params.append(int(opened_since))
        if opened_before:
            clauses.append("ts.opened_at < ?")
            params.append(int(opened_before))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT
                ts.*,
                COALESCE(NULLIF(mc.symbol, ''), NULLIF(mc.name, ''), NULLIF(ts.token_symbol, ''), substr(ts.mint, 1, 8)) AS display_symbol,
                COALESCE(NULLIF(mc.name, ''), NULLIF(mc.symbol, ''), NULLIF(ts.token_symbol, ''), substr(ts.mint, 1, 8)) AS display_name,
                ta.price_current,
                ta.market_cap_current,
                ta.market_cap_highest,
                ta.market_cap_highest_at_ts,
                ta.migrated_at,
                ta.lifecycle_stage,
                tps.prediction_score,
                tps.risk_level,
                tps.prediction_label,
                tps.prediction_status,
                tps.prediction_confidence,
                CASE
                    WHEN ta.market_cap_highest >= 5000000 THEN 'G1'
                    WHEN ta.market_cap_highest >= 2000000 THEN 'G2'
                    WHEN ta.market_cap_highest >= 500000  THEN 'G3'
                    WHEN ta.market_cap_highest >= 300000  THEN 'G4'
                    WHEN ta.market_cap_highest >= 150000  THEN 'G5'
                    WHEN ta.market_cap_highest >= 75000   THEN 'G6'
                    WHEN ta.market_cap_highest IS NOT NULL THEN 'G7'
                    ELSE NULL
                END AS g_level,
                COALESCE((SELECT MAX(tpa.liquidity_removed) FROM token_pool_accounts tpa WHERE tpa.mint = ts.mint), 0) AS liquidity_removed
            FROM trade_simulations ts
            LEFT JOIN token_analysis ta ON ta.mint = ts.mint
            LEFT JOIN metadata_cache mc ON mc.mint = ts.mint
            LEFT JOIN token_prediction_scores tps ON tps.mint = ts.mint
            {where}
            ORDER BY ts.opened_at DESC, ts.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [self._row_to_dict(row, strategy=strategy, conn=conn) for row in rows]

    def summarize_simulations(
        self,
        conn,
        risk_level: Optional[str] = None,
        opened_since: Optional[int] = None,
        opened_before: Optional[int] = None,
        strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema(conn)
        params: list[Any] = []
        clauses: list[str] = []
        if risk_level:
            clauses.append("COALESCE(tps.risk_level, 'UNKNOWN') = ?")
            params.append(risk_level.upper())
        if opened_since:
            clauses.append("ts.opened_at >= ?")
            params.append(int(opened_since))
        if opened_before:
            clauses.append("ts.opened_at < ?")
            params.append(int(opened_before))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        summary = dict(conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN ts.status = 'OPEN' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN ts.status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_count,
                SUM(ts.entry_sol) AS total_entry_sol,
                SUM(CASE WHEN ts.status = 'CLOSED' THEN ts.exit_sol ELSE 0 END) AS total_exit_sol,
                SUM(CASE WHEN ts.status = 'CLOSED' THEN ts.pnl_sol ELSE 0 END) AS realized_pnl_sol,
                AVG(CASE WHEN ts.status = 'CLOSED' THEN ts.pnl_pct END) AS avg_realized_pnl_pct,
                SUM(CASE WHEN ts.notes LIKE '%auto_paper_buy_on_migration%' THEN 1 ELSE 0 END) AS migration_buy_count,
                SUM(CASE WHEN ts.notes LIKE '%auto_migration_batch%' THEN 1 ELSE 0 END) AS migration_batch_buy_count
            FROM trade_simulations ts
            LEFT JOIN token_prediction_scores tps ON tps.mint = ts.mint
            {where}
            """,
            params,
        ).fetchone())
        by_risk_rows = conn.execute(
            f"""
            SELECT
                COALESCE(tps.risk_level, 'UNKNOWN') AS risk_level,
                COUNT(*) AS count,
                SUM(CASE WHEN ts.status = 'OPEN' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN ts.status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_count,
                SUM(CASE WHEN ts.status = 'CLOSED' THEN ts.pnl_sol ELSE 0 END) AS realized_pnl_sol,
                AVG(CASE WHEN ts.status = 'CLOSED' THEN ts.pnl_pct END) AS avg_realized_pnl_pct
            FROM trade_simulations ts
            LEFT JOIN token_prediction_scores tps ON tps.mint = ts.mint
            {where}
            GROUP BY COALESCE(tps.risk_level, 'UNKNOWN')
            ORDER BY count DESC, risk_level ASC
            """,
            params,
        ).fetchall()
        summary["by_risk"] = [dict(row) for row in by_risk_rows]

        simulation_rows = conn.execute(
            f"""
            SELECT
                ts.*,
                ta.price_current,
                ta.market_cap_current,
                ta.market_cap_highest,
                ta.market_cap_highest_at_ts
            FROM trade_simulations ts
            LEFT JOIN token_analysis ta ON ta.mint = ts.mint
            LEFT JOIN token_prediction_scores tps ON tps.mint = ts.mint
            {where}
            """,
            params,
        ).fetchall()
        positions = [self._row_to_dict(row, strategy=strategy, conn=conn) for row in simulation_rows]
        open_positions = [row for row in positions if row.get("status") == "OPEN"]
        closed_positions = [row for row in positions if row.get("status") == "CLOSED"]
        realized_pnl_usd = sum(
            float(row["pnl_usd"])
            for row in closed_positions
            if row.get("pnl_usd") is not None
        )
        unrealized_pnl_usd = sum(
            float(row["unrealized_pnl_usd"])
            for row in open_positions
            if row.get("unrealized_pnl_usd") is not None
        )
        unrealized_value_usd = sum(
            float(row["unrealized_value_usd"])
            for row in open_positions
            if row.get("unrealized_value_usd") is not None
        )
        unrealized_pnl_sol = sum(
            float(row["unrealized_pnl_sol"])
            for row in open_positions
            if row.get("unrealized_pnl_sol") is not None
        )
        summary["realized_pnl_usd"] = realized_pnl_usd
        summary["unrealized_pnl_usd"] = unrealized_pnl_usd
        summary["unrealized_value_usd"] = unrealized_value_usd
        summary["unrealized_pnl_sol"] = unrealized_pnl_sol
        summary["current_pnl_usd"] = realized_pnl_usd + unrealized_pnl_usd
        summary["current_pnl_sol"] = float(summary.get("realized_pnl_sol") or 0) + unrealized_pnl_sol
        strategy_pnl_usd = sum(
            float(row["strategy_pnl_usd"])
            for row in positions
            if row.get("strategy_pnl_usd") is not None
        )
        summary["strategy"] = _normalize_strategy(strategy)
        summary["strategy_label"] = STRATEGY_NAMES.get(summary["strategy"], "Current")
        summary["strategy_pnl_usd"] = strategy_pnl_usd

        for key, value in list(summary.items()):
            if value is None:
                summary[key] = 0
        return summary

    def reset_simulations(self, conn) -> Dict[str, int]:
        self.ensure_schema(conn)
        event_count = conn.execute("SELECT COUNT(*) FROM trade_simulation_events").fetchone()[0]
        simulation_count = conn.execute("SELECT COUNT(*) FROM trade_simulations").fetchone()[0]
        conn.execute("DELETE FROM trade_simulation_events")
        conn.execute("DELETE FROM trade_simulations")
        conn.commit()
        return {
            "deleted_events": int(event_count or 0),
            "deleted_simulations": int(simulation_count or 0),
        }

    def get_simulation(self, conn, simulation_id: int) -> Optional[Dict[str, Any]]:
        self.ensure_schema(conn)
        row = conn.execute(
            """
            SELECT
                ts.*,
                COALESCE(NULLIF(mc.symbol, ''), NULLIF(mc.name, ''), NULLIF(ts.token_symbol, ''), substr(ts.mint, 1, 8)) AS display_symbol,
                COALESCE(NULLIF(mc.name, ''), NULLIF(mc.symbol, ''), NULLIF(ts.token_symbol, ''), substr(ts.mint, 1, 8)) AS display_name,
                ta.price_current,
                ta.market_cap_current,
                ta.market_cap_highest,
                ta.market_cap_highest_at_ts,
                ta.migrated_at,
                ta.lifecycle_stage,
                tps.prediction_score,
                tps.risk_level,
                tps.prediction_label,
                tps.prediction_status,
                tps.prediction_confidence,
                CASE
                    WHEN ta.market_cap_highest >= 5000000 THEN 'G1'
                    WHEN ta.market_cap_highest >= 2000000 THEN 'G2'
                    WHEN ta.market_cap_highest >= 500000  THEN 'G3'
                    WHEN ta.market_cap_highest >= 300000  THEN 'G4'
                    WHEN ta.market_cap_highest >= 150000  THEN 'G5'
                    WHEN ta.market_cap_highest >= 75000   THEN 'G6'
                    WHEN ta.market_cap_highest IS NOT NULL THEN 'G7'
                    ELSE NULL
                END AS g_level
            FROM trade_simulations ts
            LEFT JOIN token_analysis ta ON ta.mint = ts.mint
            LEFT JOIN metadata_cache mc ON mc.mint = ts.mint
            LEFT JOIN token_prediction_scores tps ON tps.mint = ts.mint
            WHERE ts.id = ?
            """,
            (int(simulation_id),),
        ).fetchone()
        if not row:
            return None
        payload = self._row_to_dict(row, conn=conn)
        events = conn.execute(
            """
            SELECT event_type, payload_json, created_at
            FROM trade_simulation_events
            WHERE simulation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (int(simulation_id),),
        ).fetchall()
        payload["events"] = [
            {
                "event_type": event["event_type"],
                "payload": json.loads(event["payload_json"] or "{}"),
                "created_at": event["created_at"],
            }
            for event in events
        ]
        return payload

    def _strategy_history(self, conn, mint: str, opened_at: int, entry_market_cap: Optional[float]) -> Dict[str, Any]:
        history = {
            "stop_hit": False,
            "stop_ratio": None,
            "stop_label": None,
            "stop_market_cap": None,
            "stop_at": None,
            "target_first_hit_at": {},
        }
        if not conn or not mint or not opened_at or not entry_market_cap:
            return history
        max_window = max(rule["window_seconds"] for rule in STRATEGY_STOP_RULES)
        rows = conn.execute(
            """
            SELECT captured_at, market_cap
            FROM token_price_snapshots
            WHERE mint = ?
              AND captured_at >= ?
              AND captured_at <= ?
              AND market_cap > 0
            ORDER BY captured_at ASC
            """,
            (mint, int(opened_at), int(opened_at + max_window)),
        ).fetchall()
        for row in rows:
            captured_at = int(row["captured_at"])
            market_cap = _safe_float(row["market_cap"])
            if not market_cap:
                continue
            ratio = market_cap / entry_market_cap
            elapsed = captured_at - int(opened_at)
            for target in STRATEGY_CASCADE_TARGETS:
                if ratio >= target and target not in history["target_first_hit_at"]:
                    history["target_first_hit_at"][target] = captured_at
            if not history["stop_hit"]:
                for rule in STRATEGY_STOP_RULES:
                    if elapsed <= int(rule["window_seconds"]) and ratio <= float(rule["ratio"]):
                        history.update(
                            {
                                "stop_hit": True,
                                "stop_ratio": float(rule["ratio"]),
                                "stop_label": str(rule["label"]),
                                "stop_market_cap": market_cap,
                                "stop_at": captured_at,
                            }
                        )
                        break
        return history

    def _row_to_dict(self, row, strategy: Optional[str] = None, conn=None) -> Dict[str, Any]:
        data = dict(row)
        for key in ("entry_quote_json", "exit_quote_json"):
            raw = data.get(key)
            data[key.replace("_json", "")] = json.loads(raw) if raw else None
            data.pop(key, None)
        entry_market_cap = _safe_float(data.get("entry_market_cap"))
        token_amount_ui = _safe_float(data.get("token_amount_ui"))
        entry_usd = _quote_swap_usd_value(data.get("entry_quote"))
        exit_usd = _quote_swap_usd_value(data.get("exit_quote"))
        entry_dry_run = data.get("entry_quote", {}).get("dry_run") if isinstance(data.get("entry_quote"), dict) else None
        data["entry_dry_run"] = entry_dry_run
        data["entry_dry_run_status"] = entry_dry_run.get("status") if isinstance(entry_dry_run, dict) else None
        if entry_usd is None and entry_market_cap and token_amount_ui:
            entry_usd = entry_market_cap * token_amount_ui / DEFAULT_PUMPFUN_SUPPLY_UI
        data["entry_usd"] = entry_usd
        data["exit_usd"] = exit_usd
        if entry_market_cap is None and token_amount_ui:
            if entry_usd:
                entry_market_cap = entry_usd / token_amount_ui * DEFAULT_PUMPFUN_SUPPLY_UI
                data["entry_market_cap"] = entry_market_cap
                data["entry_market_cap_source"] = data.get("entry_market_cap_source") or "entry_quote.swapUsdValue"

        current_market_cap = _safe_float(data.get("market_cap_current"))
        entry_sol = _safe_float(data.get("entry_sol"))
        if data.get("status") == "OPEN" and current_market_cap and entry_market_cap and entry_sol:
            value_ratio = current_market_cap / entry_market_cap
            unrealized_value_sol = entry_sol * value_ratio
            unrealized_pnl_sol = unrealized_value_sol - entry_sol
            data["unrealized_value_sol"] = unrealized_value_sol
            data["unrealized_pnl_sol"] = unrealized_pnl_sol
            data["unrealized_pnl_pct"] = (value_ratio - 1.0) * 100.0
            if entry_usd:
                unrealized_value_usd = current_market_cap * token_amount_ui / DEFAULT_PUMPFUN_SUPPLY_UI
                data["unrealized_value_usd"] = unrealized_value_usd
                data["unrealized_pnl_usd"] = unrealized_value_usd - entry_usd
            else:
                data["unrealized_value_usd"] = None
                data["unrealized_pnl_usd"] = None
        else:
            data["unrealized_value_sol"] = None
            data["unrealized_pnl_sol"] = None
            data["unrealized_pnl_pct"] = None
            data["unrealized_value_usd"] = None
            data["unrealized_pnl_usd"] = None
        if exit_usd is not None and entry_usd is not None:
            data["pnl_usd"] = exit_usd - entry_usd
        else:
            data["pnl_usd"] = None
        strategy_history = self._strategy_history(
            conn,
            str(data.get("mint") or ""),
            int(data.get("opened_at") or 0),
            entry_market_cap,
        ) if conn is not None else None
        self._apply_strategy_view(data, strategy, strategy_history=strategy_history)
        return data

    def _apply_strategy_view(self, data: Dict[str, Any], strategy: Optional[str], strategy_history: Optional[Dict[str, Any]] = None) -> None:
        strategy_key = _normalize_strategy(strategy)
        entry_usd = _safe_float(data.get("entry_usd"))
        entry_market_cap = _safe_float(data.get("entry_market_cap"))
        current_market_cap = _safe_float(data.get("market_cap_current"))
        peak_market_cap = _safe_float(data.get("market_cap_highest"))

        data["strategy"] = strategy_key
        data["strategy_label"] = STRATEGY_NAMES.get(strategy_key, "Current")
        data["strategy_value_usd"] = None
        data["strategy_pnl_usd"] = None
        data["strategy_pnl_pct"] = None
        data["strategy_detail"] = "unavailable"
        data["strategy_hit"] = None
        data["strategy_sold_pct"] = 0.0
        data["strategy_sold_usd"] = 0.0
        data["strategy_sold_token_amount"] = 0.0
        data["strategy_stop_hit"] = False
        data["strategy_stop_label"] = None
        data["strategy_stop_market_cap"] = None
        data["strategy_stop_at"] = None

        if not entry_usd or not entry_market_cap:
            return

        current_ratio = current_market_cap / entry_market_cap if current_market_cap else None
        peak_ratio = peak_market_cap / entry_market_cap if peak_market_cap else None
        actual_exit_usd = _safe_float(data.get("exit_usd"))
        actual_pnl_usd = _safe_float(data.get("pnl_usd"))
        actual_pnl_pct = _safe_float(data.get("pnl_pct"))
        token_amount_ui = _safe_float(data.get("token_amount_ui")) or 0.0

        value_ratio = current_ratio
        detail = "current"
        hit = None
        sold_ratio = 0.0
        sold_value_ratio = 0.0
        stop_hit = bool(strategy_history and strategy_history.get("stop_hit"))
        stop_ratio = _safe_float(strategy_history.get("stop_ratio")) if stop_hit else None
        stop_at = int(strategy_history.get("stop_at") or 0) if stop_hit else None

        if strategy_key == "current":
            if data.get("status") == "CLOSED" and actual_exit_usd is not None:
                data["strategy_value_usd"] = actual_exit_usd
                data["strategy_pnl_usd"] = actual_pnl_usd
                data["strategy_pnl_pct"] = actual_pnl_pct
                data["strategy_detail"] = "actual exit"
                data["strategy_hit"] = True
                data["strategy_sold_pct"] = 100.0
                data["strategy_sold_usd"] = actual_exit_usd
                data["strategy_sold_token_amount"] = token_amount_ui
                return
        elif strategy_key == "peak":
            if stop_hit and stop_ratio is not None:
                value_ratio = stop_ratio
                detail = f"stop {strategy_history.get('stop_label')}"
                hit = False
                sold_ratio = 1.0
                sold_value_ratio = stop_ratio
            else:
                value_ratio = peak_ratio or current_ratio
                detail = "peak"
                hit = peak_ratio is not None
                if peak_ratio is not None:
                    sold_ratio = 1.0
                    sold_value_ratio = peak_ratio
        elif strategy_key == "cascade":
            if current_ratio is None and peak_ratio is None:
                return
            fallback_ratio = current_ratio or peak_ratio or 1.0
            target_first_hit_at = (strategy_history or {}).get("target_first_hit_at") or {}
            sold_ratios = [
                target
                for target in STRATEGY_CASCADE_TARGETS
                if (
                    peak_ratio is not None
                    and peak_ratio >= target
                    and (
                        not stop_hit
                        or target_first_hit_at.get(target, 0)
                        and int(target_first_hit_at[target]) <= stop_at
                    )
                )
            ]
            unsold_count = len(STRATEGY_CASCADE_TARGETS) - len(sold_ratios)
            remaining_ratio = stop_ratio if stop_hit and stop_ratio is not None else fallback_ratio
            value_ratio = (sum(sold_ratios) + (unsold_count * remaining_ratio)) / len(STRATEGY_CASCADE_TARGETS)
            sold_ratio = len(sold_ratios) / len(STRATEGY_CASCADE_TARGETS)
            if stop_hit:
                sold_ratio = 1.0
            sold_value_ratio = (sum(sold_ratios) + (unsold_count * remaining_ratio if stop_hit else 0.0)) / len(STRATEGY_CASCADE_TARGETS)
            detail = (
                f"cascade {len(sold_ratios)}/{len(STRATEGY_CASCADE_TARGETS)} hit + stop {strategy_history.get('stop_label')}"
                if stop_hit
                else f"cascade {len(sold_ratios)}/{len(STRATEGY_CASCADE_TARGETS)} hit"
            )
            hit = bool(sold_ratios)
        else:
            target = STRATEGY_TARGETS.get(strategy_key)
            if target is not None:
                target_first_hit_at = ((strategy_history or {}).get("target_first_hit_at") or {}).get(target)
                target_hit = (
                    peak_ratio is not None
                    and peak_ratio >= target
                    and (not stop_hit or (target_first_hit_at and int(target_first_hit_at) <= stop_at))
                )
                value_ratio = target if target_hit else (stop_ratio if stop_hit and stop_ratio is not None else current_ratio)
                detail = (
                    f"{target:g}x hit"
                    if target_hit
                    else (f"stop {strategy_history.get('stop_label')}" if stop_hit else f"{target:g}x not hit")
                )
                hit = target_hit
                if target_hit:
                    sold_ratio = 1.0
                    sold_value_ratio = target
                elif stop_hit and stop_ratio is not None:
                    sold_ratio = 1.0
                    sold_value_ratio = stop_ratio

        if value_ratio is None:
            return

        strategy_value_usd = entry_usd * value_ratio
        data["strategy_value_usd"] = strategy_value_usd
        data["strategy_pnl_usd"] = strategy_value_usd - entry_usd
        data["strategy_pnl_pct"] = (value_ratio - 1.0) * 100.0
        data["strategy_detail"] = detail
        data["strategy_hit"] = hit
        data["strategy_sold_pct"] = sold_ratio * 100.0
        data["strategy_sold_usd"] = entry_usd * sold_value_ratio
        data["strategy_sold_token_amount"] = token_amount_ui * sold_ratio
        if stop_hit:
            data["strategy_stop_hit"] = True
            data["strategy_stop_label"] = strategy_history.get("stop_label")
            data["strategy_stop_market_cap"] = strategy_history.get("stop_market_cap")
            data["strategy_stop_at"] = strategy_history.get("stop_at")
