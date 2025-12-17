import sqlite3
import json
import asyncio
import websockets
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from flask import Flask, jsonify, Response
from threading import Thread
import threading
import base64
import struct
import base58
import requests
import time
import queue
import sys

# Helius RPC endpoints (rotate if rate limited)
#RPC_HTTPS_URL = "https://mainnet.helius-rpc.com/?api-key=f084fae8-d111-4337-9960-2d9c5e02a726"  # MARZEL
RPC_HTTPS_URL = "https://mainnet.helius-rpc.com/?api-key=0ae07551-32df-4d9d-af2a-1925fb7f561f"  # JEZZA
#RPC_HTTPS_URL = "https://mainnet.helius-rpc.com/?api-key=a132b19d-9b44-4c71-8e6f-d320d9f351c6"  # GITHUB

# Known quote tokens for smart price pair selection
KNOWN_QUOTES = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWaLb3odccjf2cj6zpf5p6A8wMJvhystNRepAHRA": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenErt": "USDT",
    "BXXx3hqV7pZGT3XKRsvB9LyXYH2JjHVHwp4GbpNXYL3": "PAI",
}


class MeteoraPriceFetcher:
    """Fetch prices from Meteora DAMM V2 pools and compare with DexScreener"""

    def __init__(self):
        self.rpc_url = RPC_HTTPS_URL
        self.sol_mint = "So11111111111111111111111111111111111111112"

    def rpc_call(self, method: str, params: list) -> Optional[Dict]:
        """Make RPC call to Helius"""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            response = requests.post(self.rpc_url, json=payload, timeout=15)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            print(f"[RPC] Error: {e}")
            return None

    def get_dexscreener_price(self, token_mint: str) -> Optional[Dict]:
        """Get price data from DexScreener API"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "pairs" in data and data["pairs"]:
                    pair = data["pairs"][0]
                    return {
                        "priceNative": float(pair.get("priceNative", 0)),
                        "priceUsd": float(pair.get("priceUsd", 0)),
                        "liquidity": pair.get("liquidity", {}),
                        "pairAddress": pair.get("pairAddress"),
                        "volume24h": pair.get("volume", {}).get("h24", 0),
                        "baseToken": pair.get("baseToken", {}).get("symbol"),
                        "quoteToken": pair.get("quoteToken", {}).get("symbol"),
                    }
            return None
        except Exception:
            return None

    def fetch_price(self, pool_address_or_mint: str) -> Optional[Dict]:
        """Fetch price from Meteora pool using pool creation transaction"""
        try:
            # Fetch pool creation transaction (last in history = oldest = creation)
            result = self.rpc_call("getSignaturesForAddress", [pool_address_or_mint, {"limit": 10}])
            if not result or not result.get("result"):
                return None

            sigs = result.get("result", [])
            if not sigs:
                return None

            # Get pool creation transaction (last signature = oldest = creation)
            tx_sig = sigs[-1]["signature"]

            # Extract vaults from transaction (including inner instructions)
            vaults = self._extract_vaults_from_tx(tx_sig, pool_address_or_mint)
            if not vaults or len(vaults) < 2:
                return None

            # Fetch vault balances
            token_vaults = []
            for vault_addr in vaults:
                vault_data = self._get_vault_info(vault_addr)
                if vault_data:
                    token_vaults.append((vault_addr, vault_data))

            if len(token_vaults) < 2:
                return None

            # Calculate best price
            best_price = self._calculate_best_price(token_vaults)
            if not best_price or best_price.get("warning") == "POOL_DEPLETED":
                return None

            # Get DexScreener data
            dex_data = self.get_dexscreener_price(pool_address_or_mint)

            return {
                "on_chain_price": best_price.get("price"),
                "dexscreener_data": dex_data,
                "vault_count": len(token_vaults),
            }
        except Exception as e:
            print(f"[PRICE] Error fetching price: {e}")
            return None

    def _extract_vaults_from_tx(self, tx_sig: str, pool_address: str) -> List[str]:
        """Extract vault addresses from pool creation transaction, including inner instructions"""
        try:
            result = self.rpc_call(
                "getTransaction",
                [tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            if not result or not result.get("result"):
                return []

            tx_data = result["result"]
            accounts = tx_data["transaction"]["message"]["accountKeys"]
            account_keys = [acc["pubkey"] for acc in accounts]

            # Filter SPL token accounts from main instruction
            vaults = []
            for acc in account_keys:
                if acc != pool_address and len(acc) == 44:
                    if self._is_token_account(acc):
                        vaults.append(acc)

            # Also check inner instructions for vault references
            meta = tx_data.get("meta", {})
            inner_instructions = meta.get("innerInstructions", [])

            for inner in inner_instructions:
                for instr in inner.get("instructions", []):
                    for idx in instr.get("accounts", []):
                        if isinstance(idx, int) and idx < len(account_keys):
                            acc = account_keys[idx]
                            if acc != pool_address and acc not in vaults and len(acc) == 44:
                                if self._is_token_account(acc):
                                    vaults.append(acc)

            # Remove duplicates while preserving order
            vaults = list(dict.fromkeys(vaults))

            return vaults
        except Exception as e:
            print(f"[VAULT EXTRACT] Error extracting vaults: {e}")
            return []

    def _is_token_account(self, account_addr: str) -> bool:
        """Check if account is SPL token account"""
        try:
            result = self.rpc_call("getAccountInfo", [account_addr, {"encoding": "base64"}])
            if not result or not result.get("result"):
                return False
            acc_info = result["result"]["value"]
            if acc_info is None:
                return False
            owner = acc_info.get("owner", "")
            return owner == "TokenkegQfeZyiNwAJsyFbPVwwQQfaut1PNcZiHon8"
        except Exception:
            return False

    def _get_vault_info(self, vault_addr: str) -> Optional[Dict]:
        """Get vault balance and mint info"""
        try:
            result = self.rpc_call("getAccountInfo", [vault_addr, {"encoding": "base64"}])
            if not result or not result.get("result"):
                return None

            acc_info = result["result"]["value"]
            if not acc_info or not acc_info.get("data"):
                return None

            account_data = base64.b64decode(acc_info["data"][0])
            if len(account_data) < 165:
                return None

            # Parse SPL token account
            mint = base58.b58encode(account_data[0:32]).decode()
            amount = struct.unpack("<Q", account_data[64:72])[0]
            decimals_byte = account_data[72]

            # Get mint decimals
            decimals = self._get_mint_decimals(mint)
            human_balance = amount / (10 ** (decimals or 6))

            return {"mint": mint, "amount": amount, "decimals": decimals or 6, "human": human_balance}
        except Exception:
            return None

    def _get_mint_decimals(self, mint_address: str) -> Optional[int]:
        """Get token decimals from mint account"""
        try:
            result = self.rpc_call("getAccountInfo", [mint_address, {"encoding": "base64"}])
            if not result or not result.get("result"):
                return None
            acc_info = result["result"]["value"]
            if not acc_info or not acc_info.get("data"):
                return None
            account_data = base64.b64decode(acc_info["data"][0])
            if len(account_data) < 45:
                return None
            return account_data[44]
        except Exception:
            return None

    def _calculate_best_price(self, token_vaults: List[Tuple[str, Dict]]) -> Optional[Dict]:
        """Calculate best price from vault pairs with smart selection"""
        if len(token_vaults) < 2:
            return None

        best_result = None
        best_is_quote_pair = False

        for i in range(len(token_vaults)):
            for j in range(i + 1, len(token_vaults)):
                vault_i, info_i = token_vaults[i]
                vault_j, info_j = token_vaults[j]

                if info_i["human"] <= 0 or info_j["human"] <= 0:
                    continue

                is_i_quote = (
                    info_i["mint"] in KNOWN_QUOTES or info_i["mint"] == self.sol_mint
                )
                is_j_quote = (
                    info_j["mint"] in KNOWN_QUOTES or info_j["mint"] == self.sol_mint
                )

                # Try both directions
                price_j_per_i = info_j["human"] / info_i["human"]
                price_i_per_j = info_i["human"] / info_j["human"]

                # Process j/i direction
                if price_j_per_i > 0:
                    this_is_quote_pair = is_j_quote
                    should_update = False

                    if best_result is None:
                        should_update = True
                    elif this_is_quote_pair and not best_is_quote_pair:
                        should_update = True
                    elif this_is_quote_pair == best_is_quote_pair:
                        best_base_balance = token_vaults[best_result["base_idx"]][1]["human"]
                        if info_i["human"] > best_base_balance * 1.1:
                            should_update = True
                        elif (
                            abs(info_i["human"] - best_base_balance) / best_base_balance
                            < 0.1
                        ):
                            should_update = (
                                abs(price_j_per_i - 1)
                                < abs(best_result["price"] - 1)
                            )

                    if should_update:
                        best_result = {
                            "price": price_j_per_i,
                            "base_idx": i,
                            "quote_idx": j,
                        }
                        best_is_quote_pair = this_is_quote_pair

        return best_result


class RaydiumDatabase:
    """Handle SQLite database operations for Raydium pools"""

    def __init__(self, db_name: str = "raydium_pools.db"):
        self.db_name = db_name
        self.init_database()

    def get_connection(self) -> sqlite3.Connection:
        """Get a new database connection for each operation"""
        return sqlite3.connect(self.db_name, check_same_thread=False)

    def init_database(self):
        """Initialize database and create tables"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amm_id TEXT UNIQUE NOT NULL,
                name TEXT,
                symbol TEXT,
                image TEXT,
                base_mint TEXT,
                quote_mint TEXT,
                liquidity REAL,
                volume_24h REAL,
                price REAL,
                apr REAL,
                signature TEXT,
                dex TEXT,
                first_seen TIMESTAMP,
                last_updated TIMESTAMP,
                creation_price REAL,
                current_price REAL,
                last_price_update TIMESTAMP,
                total_supply REAL,
                market_cap REAL
            )
        ''')

        # Add new columns if they don't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN symbol TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN image TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN signature TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN dex TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN creation_price REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN current_price REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN last_price_update TIMESTAMP')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN total_supply REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN market_cap REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN dexscreener_price_usd REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN dexscreener_price_native REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN last_dexscreener_update TIMESTAMP')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN sol_usd_price REAL')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN is_depleted BOOLEAN')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE pools ADD COLUMN depletion_reason TEXT')
        except sqlite3.OperationalError:
            pass

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_amm_id ON pools(amm_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_first_seen ON pools(first_seen)
        ''')

        conn.commit()
        conn.close()
        print(f"Database initialized: {self.db_name}")

    def pool_exists(self, amm_id: str) -> bool:
        """Check if pool already exists in database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM pools WHERE amm_id = ?', (amm_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def insert_pool(self, pool: Dict) -> bool:
        """Insert new pool into database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO pools (
                    amm_id, name, symbol, image, base_mint, quote_mint,
                    liquidity, volume_24h, price, apr, signature, dex,
                    first_seen, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pool.get('ammId'),
                pool.get('name'),
                pool.get('symbol', ''),
                pool.get('image', ''),
                pool.get('baseMint'),
                pool.get('quoteMint'),
                pool.get('liquidity', 0),
                pool.get('volume24h', 0),
                pool.get('price', 0),
                pool.get('apr', 0),
                pool.get('signature', ''),
                pool.get('dex', 'Unknown'),
                timestamp,
                timestamp
            ))

            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"Error inserting pool: {e}")
            return False
        finally:
            conn.close()


    def get_pool_count(self) -> int:
        """Get total number of pools in database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM pools')
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_recent_pools(self, limit: int = 50) -> List[Dict]:
        """Get most recently added pools"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT amm_id, name, symbol, image, base_mint, liquidity, price, signature, dex, first_seen, creation_price, current_price, dexscreener_price_usd, dexscreener_price_native, sol_usd_price
            FROM pools
            ORDER BY first_seen DESC
            LIMIT ?
        ''', (limit,))

        results = []
        for row in cursor.fetchall():
            results.append({
                'amm_id': row[0],
                'name': row[1],
                'symbol': row[2] or '',
                'image': row[3] or '',
                'base_mint': row[4] or '',
                'liquidity': row[5],
                'price': row[6],
                'signature': row[7] or '',
                'dex': row[8] or 'Unknown',
                'first_seen': row[9],
                'creation_price': row[10],
                'current_price': row[11],
                'dexscreener_price_usd': row[12],
                'dexscreener_price_native': row[13],
                'sol_usd_price': row[14]
            })
        conn.close()
        return results

    def update_pool_price(self, amm_id: str, current_price: float, is_initial: bool = False) -> bool:
        """Update current price for a pool

        If is_initial=True, also sets creation_price (the price at pool creation).
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            if is_initial:
                # First price update - set both creation_price and current_price
                cursor.execute('''
                    UPDATE pools
                    SET creation_price = ?, current_price = ?, last_price_update = ?
                    WHERE amm_id = ?
                ''', (current_price, current_price, timestamp, amm_id))
            else:
                # Subsequent price update - only update current_price
                cursor.execute('''
                    UPDATE pools
                    SET current_price = ?, last_price_update = ?
                    WHERE amm_id = ?
                ''', (current_price, timestamp, amm_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating pool price: {e}")
            return False
        finally:
            conn.close()

    def update_pool_supply_and_price(self, amm_id: str, total_supply: float, price: float, is_initial: bool = False) -> bool:
        """Update total supply and current price, calculate market cap

        If is_initial=True, also sets creation_price (initial price at pool creation)
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            market_cap = total_supply * price if total_supply and price else None

            if is_initial:
                # First price update - set both creation_price and current_price
                cursor.execute('''
                    UPDATE pools
                    SET total_supply = ?, creation_price = ?, current_price = ?, market_cap = ?, last_price_update = ?
                    WHERE amm_id = ?
                ''', (total_supply, price, price, market_cap, timestamp, amm_id))
            else:
                # Subsequent updates - only update current_price
                cursor.execute('''
                    UPDATE pools
                    SET total_supply = ?, current_price = ?, market_cap = ?, last_price_update = ?
                    WHERE amm_id = ?
                ''', (total_supply, price, market_cap, timestamp, amm_id))

            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating pool supply/price: {e}")
            return False
        finally:
            conn.close()

    def update_dexscreener_price(self, amm_id: str, price_usd: float, price_native: float = None) -> bool:
        """Update DexScreener price data for a pool and calculate SOL/USD rate"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()

            # Calculate SOL/USD rate if both prices available
            sol_usd_rate = None
            if price_native and price_native > 0 and price_usd:
                sol_usd_rate = price_usd / price_native

            cursor.execute('''
                UPDATE pools
                SET dexscreener_price_usd = ?, dexscreener_price_native = ?, last_dexscreener_update = ?, sol_usd_price = ?
                WHERE amm_id = ?
            ''', (price_usd, price_native, timestamp, sol_usd_rate, amm_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating DexScreener price: {e}")
            return False
        finally:
            conn.close()

    def update_depletion_status(self, amm_id: str, is_depleted: bool, reason: str = None) -> bool:
        """Update pool depletion status"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE pools
                SET is_depleted = ?, depletion_reason = ?
                WHERE amm_id = ?
            ''', (is_depleted, reason, amm_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating depletion status: {e}")
            return False
        finally:
            conn.close()

    def get_pools_needing_update(self) -> List[Dict]:
        """Get pools that need price updates based on age and last update time"""
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now()

        # Get pools by age with their update needs
        cursor.execute('''
            SELECT amm_id, base_mint, first_seen, last_price_update, creation_price, current_price, signature
            FROM pools
            WHERE base_mint IS NOT NULL AND base_mint != ''
            ORDER BY first_seen DESC
        ''')

        results = []
        for row in cursor.fetchall():
            amm_id, base_mint, first_seen, last_update, creation_price, current_price, signature = row

            first_seen_dt = datetime.fromisoformat(first_seen)
            age_seconds = (now - first_seen_dt).total_seconds()

            # Determine update interval based on age
            if age_seconds < 300:  # 0-5 minutes: update every 30 seconds
                update_interval = 30
            elif age_seconds < 1800:  # 5-30 minutes: update every 2 minutes
                update_interval = 120
            else:  # 30+ minutes: update every 5 minutes
                update_interval = 300

            # Check if needs update
            if last_update is None:
                needs_update = True
            else:
                last_update_dt = datetime.fromisoformat(last_update)
                seconds_since_update = (now - last_update_dt).total_seconds()
                needs_update = seconds_since_update >= update_interval

            if needs_update:
                results.append({
                    'amm_id': amm_id,
                    'base_mint': base_mint,
                    'creation_price': creation_price,
                    'current_price': current_price,
                    'age_seconds': age_seconds,
                    'signature': signature
                })

        conn.close()
        return results

    def delete_all_pools(self) -> int:
        """Delete all entries from pools table. Returns number of deleted rows."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM pools')
        pools_count = cursor.fetchone()[0]
        
        cursor.execute('DELETE FROM pools')
        
        # Delete pool_history if it exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pool_history'")
        if cursor.fetchone():
            cursor.execute('DELETE FROM pool_history')
        
        conn.commit()
        conn.close()
        
        print(f"Deleted {pools_count} pools from database")
        return pools_count


class RaydiumMonitor:
    """Monitor Raydium DEX for new liquidity pools via Solana WebSocket"""

    # Raydium AMM Program IDs
    RAYDIUM_V4_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
    RAYDIUM_CPMM_PROGRAM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"

    # Meteora program IDs
    METEORA_PROGRAM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
    METEORA_ALT_PROGRAM = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"  # Alternative Meteora pool variant

    def __init__(self, db_name: str = "raydium_pools.db"):
        self.db = RaydiumDatabase(db_name)
        # Use Helius RPC (configured at top of file)
        self.rpc_http_url = RPC_HTTPS_URL
        self.rpc_ws_url = RPC_HTTPS_URL.replace('https://', 'wss://').replace('http://', 'ws://')
        self.is_running = False
        self.loop = None
        
        print(f"Using RPC: {self.rpc_http_url.split('?')[0]}...")

    def parse_pool_from_logs(self, logs: List[str], signature: str, dex: str = "Unknown") -> Dict:
        """Parse pool creation from transaction logs and fetch token addresses"""
        import time

        print(f"[POOL PARSE] Starting parse_pool_from_logs with dex={dex}, signature={signature[:16]}...")

        WSOL = "So11111111111111111111111111111111111111112"

        # Capture the time when the pool was first detected (from logs)
        pool_detected_time = datetime.now().isoformat()

        pool_data = {
            'ammId': signature[:16],
            'name': 'Unknown',
            'baseMint': '',
            'quoteMint': '',
            'liquidity': 0,
            'price': 0,
            'signature': signature,
            'symbol': '',
            'image': '',
            'first_seen': pool_detected_time  # Pool creation time - when we detected it
        }

        # Wait for transaction to be confirmed and indexed on chain
        # New accounts created in the transaction may take time to be queryable via RPC
        time.sleep(5)

        # Fetch full transaction to get token addresses (with retry for confirmation)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    self.rpc_http_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed"
                            }
                        ]
                    },
                    timeout=15
                )
                
                tx_data = response.json()
                
                # Check for rate limiting
                if 'error' in tx_data and tx_data['error'].get('code') == 429:
                    print(f"Rate limited, attempt {attempt + 1}/{max_retries}, waiting...")
                    time.sleep(2 ** attempt)
                    continue
                
                if 'result' in tx_data and tx_data['result']:
                    tx = tx_data['result']
                    
                    # Collect all mints that appear in the transaction
                    mint_sources = {}  # mint -> list of sources it appears in
                    
                    # Source 1: Pre and Post token balances
                    pre_balances = tx.get('meta', {}).get('preTokenBalances', [])
                    post_balances = tx.get('meta', {}).get('postTokenBalances', [])
                    
                    for balance in pre_balances:
                        mint = balance.get('mint', '')
                        if mint:
                            if mint not in mint_sources:
                                mint_sources[mint] = []
                            mint_sources[mint].append('pre_balance')
                    
                    for balance in post_balances:
                        mint = balance.get('mint', '')
                        if mint:
                            if mint not in mint_sources:
                                mint_sources[mint] = []
                            mint_sources[mint].append('post_balance')
                    
                    # Source 2: Inner instructions (TokenProgram operations)
                    inner_instructions = tx.get('meta', {}).get('innerInstructions', [])
                    for inner in inner_instructions:
                        for ix in inner.get('instructions', []):
                            if isinstance(ix, dict):
                                parsed = ix.get('parsed', {})
                                if isinstance(parsed, dict):
                                    info = parsed.get('info', {})
                                    if 'mint' in info:
                                        mint = info['mint']
                                        if mint not in mint_sources:
                                            mint_sources[mint] = []
                                        mint_sources[mint].append(f"instruction_{parsed.get('type', 'unknown')}")
                    
                    print(f"[POOL PARSE] Found {len(mint_sources)} distinct mints in transaction")
                    for mint, sources in mint_sources.items():
                        print(f"[POOL PARSE]   Mint: {mint} - Sources: {sources}")
                    print(f"[POOL PARSE] DEX type for account extraction: {dex}")

                    # Extract pool account address based on DEX type
                    account_keys = tx.get('transaction', {}).get('message', {}).get('accountKeys', [])
                    print(f"[POOL PARSE] Found {len(account_keys)} account keys in transaction")

                    # Debug: show first few account keys to understand structure
                    if account_keys:
                        sample_key = account_keys[0]
                        print(f"[POOL PARSE] Sample account key type: {type(sample_key)}, value: {str(sample_key)[:100]}")

                    pubkeys = []
                    for key in account_keys:
                        if isinstance(key, dict):
                            pubkeys.append(key.get('pubkey', ''))
                        else:
                            pubkeys.append(key)

                    print(f"[POOL PARSE] Extracted {len(pubkeys)} pubkeys")

                    # METEORA PROGRAM IDs
                    METEORA_DLMM_PROGRAM = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"  # Main DLMM program (owns LBPair)
                    METEORA_REFERRAL_PROGRAM = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"  # Referral program (not the pool)

                    # DEX-specific pool account extraction
                    pool_account_set = False
                    if dex == "Meteora":
                        # For Meteora DLMM, we need to find the LBPair account
                        # The actual LBPair is owned by the DLMM program, NOT the Referral program
                        print(f"[POOL ACCOUNT] Searching for Meteora DLMM LBPair account (owned by {METEORA_DLMM_PROGRAM[:8]}...)")
                        print(f"[POOL ACCOUNT] Will scan {len(pubkeys)} account keys")

                        lbpair_found = False
                        dlmm_accounts = []  # Accounts owned by DLMM program
                        checked_count = 0

                        for idx, candidate in enumerate(pubkeys):
                            if idx == 0:  # Skip user/signer
                                continue

                            checked_count += 1
                            # Only check first 15 accounts to avoid excessive RPC calls
                            if checked_count > 15:
                                break

                            print(f"[POOL ACCOUNT] Checking index {idx}: {candidate[:8]}...")
                            try:
                                # Query the account (retry up to 3 times with delays)
                                for attempt in range(3):
                                    check_response = requests.post(
                                        self.rpc_http_url,
                                        json={
                                            "jsonrpc": "2.0",
                                            "id": 1,
                                            "method": "getAccountInfo",
                                            "params": [candidate, {"encoding": "base64"}]
                                        },
                                        timeout=5
                                    )
                                    check_data = check_response.json()
                                    if check_data.get("result") and check_data["result"].get("value"):
                                        owner = check_data["result"]["value"].get("owner", "")
                                        encoded_data = check_data["result"]["value"].get("data", ["", ""])[0] or ""
                                        # Decode base64 account data
                                        try:
                                            account_data = base64.b64decode(encoded_data) if encoded_data else b""
                                        except Exception as decode_err:
                                            print(f"[POOL ACCOUNT]   → Failed to decode account data: {decode_err}")
                                            account_data = b""
                                        account_size = len(account_data)
                                        print(f"[POOL ACCOUNT]   → Owner: {owner[:8]}..., Size: {account_size} bytes")

                                        # Prefer DLMM program-owned accounts (these are actual LBPair pools)
                                        if owner == METEORA_DLMM_PROGRAM:
                                            dlmm_accounts.append((candidate, account_size, idx))
                                            print(f"[POOL ACCOUNT]   ✓ Found DLMM-owned account at index {idx}")
                                            if account_size > 300:  # LBPair accounts are typically 400+ bytes
                                                pool_data['ammId'] = candidate
                                                pool_account_set = True
                                                lbpair_found = True
                                                break

                                        break  # Account exists, try next
                                    else:
                                        # Account doesn't exist yet, wait and retry
                                        if attempt < 2:
                                            wait_time = 2 if attempt == 0 else 1
                                            print(f"[POOL ACCOUNT]   → Account not found, retrying in {wait_time}s...")
                                            time.sleep(wait_time)
                                        else:
                                            print(f"[POOL ACCOUNT]   → Account still not found after retries")
                                            break
                                if lbpair_found:
                                    break
                            except Exception as e:
                                print(f"[POOL ACCOUNT] ⚠ Error checking index {idx}: {e}")

                        # If we found DLMM accounts but not yet set, use the first one
                        if not lbpair_found and dlmm_accounts:
                            best_account, best_size, best_idx = dlmm_accounts[0]
                            print(f"[POOL ACCOUNT] ✓ Using DLMM account ({best_size} bytes) from index {best_idx}")
                            pool_data['ammId'] = best_account
                            pool_account_set = True
                        elif not lbpair_found:
                            print(f"[POOL ACCOUNT] ⚠ Could not find DLMM-owned LBPair account")
                    elif dex == "Raydium CPMM":
                        # For Raydium CPMM, the pool is typically at index 4 (CpmmConfig) or 5 (PoolState)
                        if len(pubkeys) > 5:
                            pool_account = pubkeys[5]
                            print(f"[POOL ACCOUNT] Raydium CPMM PoolState extracted from index 5: {pool_account}")
                            pool_data['ammId'] = pool_account
                            pool_account_set = True
                        elif len(pubkeys) > 4:
                            pool_account = pubkeys[4]
                            print(f"[POOL ACCOUNT] Raydium CPMM Config extracted from index 4: {pool_account}")
                            pool_data['ammId'] = pool_account
                            pool_account_set = True
                    else:  # Raydium V4 or Unknown
                        # For Raydium V4, the pool is at index 4
                        if len(pubkeys) > 4:
                            pool_account = pubkeys[4]
                            print(f"[POOL ACCOUNT] Raydium V4 pool extracted from index 4: {pool_account}")
                            pool_data['ammId'] = pool_account
                            pool_account_set = True

                    if not pool_account_set:
                        print(f"[POOL ACCOUNT] ✗ Failed to extract pool account for DEX={dex}, keeping signature-based ammId: {pool_data['ammId']}")
                    
                    # Identify base and quote mints
                    quote_mint = WSOL if WSOL in mint_sources else None

                    # The base mint is the new token being created
                    # It should NOT be WSOL
                    # Prefer mints that appear in postTokenBalances (actual pool state)
                    base_mint = None

                    print(f"[POOL PARSE] Identifying base mint (token address)...")
                    for mint, sources in mint_sources.items():
                        if mint == WSOL:
                            print(f"[POOL PARSE]   Skipping WSOL: {mint}")
                            continue
                        # Mints in postTokenBalances are more reliable (actual pool accounts)
                        if 'post_balance' in sources:
                            print(f"[POOL PARSE]   ✓ Found base mint with post_balance: {mint}")
                            base_mint = mint
                            break

                    # If no mint in postTokenBalances, pick first non-WSOL
                    if not base_mint:
                        print(f"[POOL PARSE]   No post_balance mints found, selecting first non-WSOL...")
                        for mint in mint_sources.keys():
                            if mint != WSOL:
                                print(f"[POOL PARSE]   ✓ Selected base mint: {mint}")
                                base_mint = mint
                                break

                    # Set mints in pool data
                    if base_mint:
                        pool_data['baseMint'] = base_mint
                        print(f"[POOL PARSE] ✓ Base mint (TOKEN ADDRESS): {base_mint}")
                    else:
                        print(f"[POOL PARSE] ✗ WARNING: No base mint found!")
                    if quote_mint:
                        pool_data['quoteMint'] = quote_mint
                        print(f"[POOL PARSE] Quote mint (WSOL): {quote_mint}")
                    
                    # Wait for metadata to be indexed on-chain (critical for fresh tokens)
                    print("Waiting for token metadata to be indexed (6 seconds)...")
                    time.sleep(6)

                    # Fetch token metadata (try on-chain Metaplex first - this is authoritative)
                    if base_mint:
                        # Try Metaplex on-chain first (crucial for brand new tokens, most reliable source)
                        metadata = self.fetch_metaplex_metadata(base_mint)

                        # Only fall back to external APIs if Metaplex lookup fails
                        if not metadata:
                            print("[METADATA] Metaplex lookup failed, trying Jupiter API (fast)...")
                            metadata = self.get_token_metadata_fast(base_mint)

                        if metadata:
                            pool_data['name'] = metadata.get('name', 'Unknown')
                            pool_data['symbol'] = metadata.get('symbol', '')
                            pool_data['image'] = metadata.get('image', '')
                            print(f"Token: {pool_data['name']} ({pool_data['symbol']})")
                        else:
                            print("No metadata found from any source - broadcasting with Unknown")
                    
                    # Successfully parsed, exit retry loop
                    return pool_data
                else:
                    # Transaction not yet available, wait and retry
                    print(f"Transaction not yet confirmed, attempt {attempt + 1}/{max_retries}...")
                    time.sleep(2 + attempt)  # Increasing delay: 2s, 3s, 4s, 5s, 6s
                        
            except Exception as e:
                print(f"Error fetching transaction details: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)

        print("Failed to fetch transaction after all retries")
        return pool_data

    def fetch_metaplex_metadata(self, mint_address: str) -> Dict:
        """Fetch Metaplex metadata directly from on-chain PDA.

        This is crucial for brand new tokens that haven't been indexed by external APIs yet.
        Uses Metaplex Token Metadata program to derive and fetch the metadata account.
        """
        try:
            from solders.pubkey import Pubkey

            TOKEN_METADATA_PROGRAM_ID = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"

            print(f"[METAPLEX] Deriving metadata PDA for {mint_address}")
            mint_pubkey = Pubkey.from_string(mint_address)
            program_pubkey = Pubkey.from_string(TOKEN_METADATA_PROGRAM_ID)

            # Derive PDA: metadata + program_id + mint
            metadata_pda, _ = Pubkey.find_program_address(
                [b"metadata", bytes(program_pubkey), bytes(mint_pubkey)],
                program_pubkey
            )

            print(f"[METAPLEX] Fetching account from RPC: {metadata_pda}")

            # Fetch the account data
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [str(metadata_pda), {"encoding": "base64"}]
            }

            response = requests.post(self.rpc_http_url, json=payload, timeout=10)
            data = response.json()

            if "error" in data:
                print(f"[METAPLEX] RPC Error: {data['error']}")
                return None

            if not data.get("result") or not data["result"].get("value"):
                print(f"[METAPLEX] Metadata account not found at PDA")
                return None

            account_info = data["result"]["value"]
            if not account_info.get("data"):
                print(f"[METAPLEX] Account has no data")
                return None

            # Decode base64 data
            account_data = base64.b64decode(account_info["data"][0])
            print(f"[METAPLEX] Decoded {len(account_data)} bytes")

            # Parse Metaplex metadata structure
            if len(account_data) < 69:
                print(f"[METAPLEX] Account data too short")
                return None

            # Check metadata key (should be 4 for MetadataV1)
            key = account_data[0]
            if key != 4:
                print(f"[METAPLEX] Invalid metadata key: {key}")
                return None

            # Parse name (offset 65-69 is name length, then name data)
            name_len = struct.unpack('<I', account_data[65:69])[0]
            name = account_data[69:69+name_len].decode('utf-8', errors='ignore').strip('\x00')

            # Parse symbol (after name)
            symbol_offset = 69 + name_len
            if symbol_offset + 4 > len(account_data):
                symbol = ""
            else:
                symbol_len = struct.unpack('<I', account_data[symbol_offset:symbol_offset+4])[0]
                symbol_offset += 4
                if symbol_offset + symbol_len > len(account_data):
                    symbol = ""
                else:
                    symbol = account_data[symbol_offset:symbol_offset+symbol_len].decode('utf-8', errors='ignore').strip('\x00')

            # Parse URI (after symbol)
            uri_offset = symbol_offset + symbol_len
            uri = ""
            if uri_offset + 4 <= len(account_data):
                uri_len = struct.unpack('<I', account_data[uri_offset:uri_offset+4])[0]
                uri_offset += 4
                if uri_offset + uri_len <= len(account_data):
                    uri = account_data[uri_offset:uri_offset+uri_len].decode('utf-8', errors='ignore').strip('\x00')

            # Try to fetch image from URI if available
            image_url = ""
            if uri:
                try:
                    print(f"[METAPLEX] Fetching metadata from URI: {uri}")
                    response = requests.get(uri, timeout=10)
                    if response.status_code == 200:
                        try:
                            metadata_json = response.json()
                            image_url = metadata_json.get('image', '')
                            if image_url:
                                print(f"[METAPLEX] ✓ Got image URL from URI: {image_url}")
                            else:
                                print(f"[METAPLEX] ✗ URI JSON has no 'image' field. Available keys: {list(metadata_json.keys())}")
                        except ValueError as je:
                            print(f"[METAPLEX] URI response is not JSON: {je}")
                            print(f"[METAPLEX] Response text: {response.text[:200]}")
                    else:
                        print(f"[METAPLEX] URI returned status {response.status_code}")
                except Exception as e:
                    print(f"[METAPLEX] Error fetching URI: {e}")

            if name or symbol:
                print(f"[METAPLEX] ✓ Found metadata: {name} ({symbol})")
                if image_url:
                    print(f"[METAPLEX] ✓ Image URL from metadata: {image_url}")
                else:
                    print(f"[METAPLEX] ✗ No image URL in metadata")
                return {
                    'name': name or 'Unknown',
                    'symbol': symbol or '',
                    'image': image_url
                }
            else:
                print(f"[METAPLEX] Metadata account found but empty name/symbol")
                return None

        except ImportError:
            print(f"[METAPLEX] solders library not available - skipping on-chain fetch")
            return None
        except Exception as e:
            print(f"[METAPLEX] Error: {e}")
            return None

    def get_token_metadata_fast(self, mint_address: str) -> Dict:
        """Fetch token metadata quickly from Jupiter API only (no retries)

        For real-time UI updates, we need speed over completeness.
        This tries only Jupiter with a single attempt for quick results.
        """
        print(f"[METADATA] Trying Jupiter API (single attempt) for {mint_address}")
        try:
            url = f"https://token.jup.ag/mint/{mint_address}"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                print(f"[METADATA] ✓ Found metadata on Jupiter: {data.get('name', 'Unknown')} ({data.get('symbol', '')})")
                image_url = data.get('logoURI', '')
                if image_url:
                    print(f"[METADATA] ✓ Image URL from Jupiter: {image_url}")
                else:
                    print(f"[METADATA] ✗ No image URL from Jupiter")
                return {
                    'name': data.get('name', 'Unknown'),
                    'symbol': data.get('symbol', ''),
                    'image': image_url
                }
            else:
                print(f"[METADATA] Jupiter API returned {response.status_code}")
        except Exception as e:
            print(f"[METADATA] Jupiter API lookup failed: {e}")

        return None

    def get_token_metadata_solana_tokens(self, mint_address: str) -> Dict:
        """Fetch token metadata from Solana token list (another fallback source)"""
        print(f"[METADATA] Trying Solana token list for {mint_address}")
        try:
            # Try Solana's official token list
            url = f"https://token.solflare.com/solana/{mint_address}"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                print(f"[METADATA] ✓ Found metadata on Solflare: {data.get('name', 'Unknown')} ({data.get('symbol', '')})")
                return {
                    'name': data.get('name', 'Unknown'),
                    'symbol': data.get('symbol', ''),
                    'image': data.get('logoURI', '') or data.get('image', '')
                }
            else:
                print(f"[METADATA] Solflare returned {response.status_code}")
        except Exception as e:
            print(f"[METADATA] Solflare lookup failed: {e}")

        return None

    def get_token_metadata_onchain(self, mint_address: str) -> Dict:
        """Fetch token metadata from multiple external API sources with fallbacks"""
        # Try DexScreener (fastest for established tokens)
        # Retry multiple times with increasing delays since it takes time to index new tokens
        print(f"[METADATA] Trying DexScreener API for {mint_address}")
        for dex_attempt in range(5):
            try:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    pairs = data.get('pairs', [])
                    
                    if pairs:
                        # Get info from the first pair (usually the most liquid)
                        pair = pairs[0]
                        base_token = pair.get('baseToken', {})
                        
                        # Check if this mint is the base or quote token
                        if base_token.get('address') == mint_address:
                            print(f"[METADATA] ✓ Found metadata on DexScreener: {base_token.get('name', 'Unknown')} ({base_token.get('symbol', '')})")
                            return {
                                'name': base_token.get('name', 'Unknown'),
                                'symbol': base_token.get('symbol', ''),
                                'image': pair.get('info', {}).get('imageUrl', '')
                            }
                        else:
                            quote_token = pair.get('quoteToken', {})
                            print(f"[METADATA] ✓ Found metadata on DexScreener: {quote_token.get('name', 'Unknown')} ({quote_token.get('symbol', '')})")
                            return {
                                'name': quote_token.get('name', 'Unknown'),
                                'symbol': quote_token.get('symbol', ''),
                                'image': ''
                            }
                    elif dex_attempt < 4:
                        # Pairs is null, retry after a longer delay
                        wait_time = 10 + (dex_attempt * 2)  # 2s, 4s, 6s, 8s, 10s
                        print(f"DexScreener returned null pairs, retrying in {wait_time}s... (attempt {dex_attempt + 2}/5)")
                        time.sleep(wait_time)
                        continue
            except Exception as e:
                print(f"DexScreener lookup failed: {e}")
        
        # Fallback: Try Jupiter API (good for new tokens)
        print(f"[METADATA] Trying Jupiter API for {mint_address}")
        try:
            url = f"https://token.jup.ag/mint/{mint_address}"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                print(f"[METADATA] ✓ Found metadata on Jupiter API: {data.get('name', 'Unknown')} ({data.get('symbol', '')})")
                return {
                    'name': data.get('name', 'Unknown'),
                    'symbol': data.get('symbol', ''),
                    'image': data.get('logoURI', '')
                }
        except Exception as e:
            print(f"[METADATA] Jupiter API lookup failed: {e}")

        # Fallback: Try Solscan API (on-chain metadata via Metaplex)
        print(f"[METADATA] Trying Solscan API for {mint_address}")
        try:
            solscan_url = f"https://api.solscan.io/token/meta?tokenAddress={mint_address}"
            response = requests.get(solscan_url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    metadata = data.get('data', {})
                    print(f"[METADATA] ✓ Found metadata on Solscan: {metadata.get('name', 'Unknown')} ({metadata.get('symbol', '')})")
                    return {
                        'name': metadata.get('name', 'Unknown'),
                        'symbol': metadata.get('symbol', ''),
                        'image': metadata.get('icon', '')
                    }
                else:
                    print(f"[METADATA] Solscan returned unsuccessful response")
        except Exception as e:
            print(f"[METADATA] Solscan lookup failed: {e}")

        # If all sources fail, return None
        print(f"[METADATA] ✗ No metadata found for {mint_address} on any source")
        return None

    def get_token_balance(self, token_account: str) -> float:
        """Fetch balance of a token account"""
        try:
            response = requests.post(
                self.rpc_http_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountBalance",
                    "params": [token_account]
                },
                timeout=10
            )

            data = response.json()

            if "error" in data or not data.get("result"):
                return 0

            amount = data["result"].get("value", {}).get("uiAmount", 0)
            return float(amount) if amount else 0

        except Exception as e:
            return 0

    def extract_vault_addresses(self, account_data: bytes) -> Dict:
        """Extract vault addresses from Meteora LBPair account data

        Meteora LBPair structure contains vault addresses:
        - vault_x (base token vault) at offset 168 (32-byte Pubkey)
        - vault_y (quote token vault) at offset 200 (32-byte Pubkey)

        These are discovered through on-chain structure analysis and represent
        the token reserves for the liquidity pool.
        """
        try:
            result = {}

            if len(account_data) > 232:  # Need at least 200 + 32 bytes for vault_y
                # Extract vault addresses (32-byte Pubkeys)
                vault_x = account_data[168:200]
                vault_y = account_data[200:232]

                vault_x_addr = base58.b58encode(vault_x).decode() if vault_x else None
                vault_y_addr = base58.b58encode(vault_y).decode() if vault_y else None

                result['vault_x'] = vault_x_addr
                result['vault_y'] = vault_y_addr

            return result
        except Exception as e:
            print(f"[VAULT EXTRACT] Error: {e}")
            return {}

    def calculate_price_from_vaults(self, vault_x_balance: float, vault_y_balance: float, base_decimals: int = 6, quote_decimals: int = 6) -> float:
        """Calculate token price from vault balances

        Price = quote_balance / base_balance
        Adjusted for token decimals
        """
        try:
            if vault_x_balance <= 0 or vault_y_balance <= 0:
                print(f"[VAULT PRICE] ⚠ One or both balances invalid: x={vault_x_balance}, y={vault_y_balance}")
                return None

            # Adjust for token decimals (convert to proper units)
            adjusted_base = vault_x_balance / (10 ** base_decimals)
            adjusted_quote = vault_y_balance / (10 ** quote_decimals)

            print(f"[VAULT PRICE] Adjusted: base={adjusted_base:,.8f}, quote={adjusted_quote:,.8f} (decimals: {base_decimals},{quote_decimals})")

            if adjusted_base <= 0:
                print(f"[VAULT PRICE] ⚠ Adjusted base is invalid")
                return None

            price = adjusted_quote / adjusted_base
            print(f"[VAULT PRICE] Calculated: {adjusted_quote:,.8f} / {adjusted_base:,.8f} = ${price:.8f}")
            return price if price > 0 else None

        except Exception as e:
            print(f"[VAULT PRICE] ✗ Error calculating price from vaults: {e}")
            import traceback
            traceback.print_exc()
            return None

    def fetch_pool_price(self, amm_id: str, base_mint: str, signature: str = None) -> Dict:
        """Fetch current price using V2 fetcher logic for proven reliability

        Returns: {'price': float, 'is_depleted': bool, 'depletion_reason': str or None}
        """
        try:
            print(f"[PRICE FETCH] Fetching price for base_mint={base_mint[:8]}... amm_id={amm_id[:8]}...")

            # Import V2 functions dynamically (cache to avoid repeated imports)
            if not hasattr(self, '_v2_fetcher_cache'):
                import importlib.util
                spec = importlib.util.spec_from_file_location("v2", "/Users/kevinkeaveney/Dev/claude/flex/meteora_price_fetcher_v2.py")
                self._v2_fetcher_cache = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self._v2_fetcher_cache)
                print(f"[PRICE FETCH] ℹ Loaded V2 fetcher module")

            v2 = self._v2_fetcher_cache

            # Use V2 fetcher which has proven working logic
            try:
                # Use fetch_price() which returns complete data structure with price, depletion info, etc.
                fetch_result = v2.fetch_price(amm_id, verbose=False)
                price = fetch_result.get('on_chain_price')

                if price is not None and price > 0:
                    print(f"[PRICE FETCH] ✓ Successfully fetched price: {price:.18f} SOL")
                    return {
                        'price': price,
                        'is_depleted': fetch_result.get('is_depleted', False),
                        'depletion_reason': fetch_result.get('depletion_reason')
                    }
                else:
                    print(f"[PRICE FETCH] ⚠ V2 fetcher returned no price (pool may be depleted or not found)")
                    return {
                        'price': None,
                        'is_depleted': fetch_result.get('is_depleted', True),
                        'depletion_reason': fetch_result.get('depletion_reason', 'No valid price returned')
                    }
            except Exception as e:
                print(f"[PRICE FETCH] ⚠ V2 fetcher error: {e}")
                import traceback
                traceback.print_exc()
                return {'price': None, 'is_depleted': False, 'depletion_reason': None}

        except Exception as e:
            print(f"[PRICE FETCH] ✗ Exception: {e}")
            import traceback
            traceback.print_exc()
            return {'price': None, 'is_depleted': False, 'depletion_reason': None}

    def _get_vault_info(self, vault_addr: str) -> dict:
        """Get vault balance and mint info from Solana RPC"""
        try:
            response = requests.post(
                self.rpc_http_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [vault_addr, {"encoding": "base64"}]
                },
                timeout=10
            )
            
            result = response.json()
            if "error" in result or not result.get("result"):
                return {}
            
            acc_info = result["result"]["value"]
            if not acc_info or not acc_info.get("data"):
                return {}
            
            try:
                account_data = base64.b64decode(acc_info["data"][0])
            except:
                return {}
            
            if len(account_data) < 72:
                return {}
            
            # Parse SPL token account structure
            try:
                mint = base58.b58encode(account_data[0:32]).decode()
                amount = struct.unpack("<Q", account_data[64:72])[0]
                decimals = self._get_mint_decimals(mint)
                
                return {
                    "mint": mint,
                    "amount": amount,
                    "decimals": decimals or 6,
                    "human": amount / (10 ** (decimals or 6))
                }
            except Exception as e:
                print(f"[VAULT INFO] Error parsing account: {e}")
                return {}
                
        except Exception as e:
            print(f"[VAULT INFO] Error fetching vault info: {e}")
            return {}

    def _get_mint_decimals(self, mint: str) -> int:
        """Get token decimals from mint account"""
        try:
            response = requests.post(
                self.rpc_http_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [mint, {"encoding": "base64"}]
                },
                timeout=10
            )
            
            result = response.json()
            if "error" in result or not result.get("result"):
                return 6  # Default fallback
            
            acc_info = result["result"]["value"]
            if not acc_info or not acc_info.get("data"):
                return 6
            
            try:
                mint_data = base64.b64decode(acc_info["data"][0])
                # Decimals field is at offset 44 in Mint account (1 byte)
                if len(mint_data) > 44:
                    decimals = mint_data[44]
                    return decimals
            except:
                pass
            
            return 6  # Default fallback
            
        except Exception as e:
            print(f"[MINT DECIMALS] Error: {e}")
            return 6  # Default fallback

    def get_token_total_supply(self, mint: str) -> Optional[float]:
        """Get total supply of a token from on-chain mint account"""
        try:
            response = requests.post(
                self.rpc_http_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [mint, {"encoding": "base64"}]
                },
                timeout=10
            )
            
            result = response.json()
            if "error" in result or not result.get("result"):
                return None
            
            acc_info = result["result"]["value"]
            if not acc_info or not acc_info.get("data"):
                return None
            
            try:
                mint_data = base64.b64decode(acc_info["data"][0])
                # Mint account structure:
                # 0-31: mint_authority (32 bytes)
                # 32-39: supply (8 bytes, u64)
                # 40-43: decimals (1 byte) + isInitialized (1 byte) + owner (4 bytes?)
                if len(mint_data) >= 40:
                    supply = struct.unpack("<Q", mint_data[32:40])[0]
                    decimals = self._get_mint_decimals(mint)
                    human_supply = supply / (10 ** (decimals or 6))
                    print(f"[TOKEN SUPPLY] {mint[:8]}...: {human_supply:,.2f} tokens (decimals: {decimals})")
                    return human_supply
            except Exception as e:
                print(f"[TOKEN SUPPLY] Error parsing mint data: {e}")
                return None
            
            return None

        except Exception as e:
            print(f"[TOKEN SUPPLY] Error fetching total supply: {e}")
            return None

    def get_dexscreener_price(self, token_mint: str) -> Optional[Dict]:
        """Get price data from DexScreener API"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "pairs" in data and data["pairs"]:
                    pair = data["pairs"][0]
                    return {
                        "priceNative": float(pair.get("priceNative", 0)),
                        "priceUsd": float(pair.get("priceUsd", 0)),
                        "liquidity": pair.get("liquidity", {}),
                        "pairAddress": pair.get("pairAddress"),
                        "volume24h": pair.get("volume", {}).get("h24", 0),
                        "baseToken": pair.get("baseToken", {}).get("symbol"),
                        "quoteToken": pair.get("quoteToken", {}).get("symbol"),
                    }
            return None
        except Exception:
            return None

    def parse_meteora_pool_price(self, account_data: bytes, pool_id: str) -> float:
        """Parse Meteora DLMM pool account data to extract current price
        
        Note: This method receives raw account data. For more reliable vault extraction,
        use extract_pool_price_from_transaction() which fetches vaults from creation TX.
        
        This method tries the bin_id formula as fallback.
        Returns: quote token amount per 1 base token (SOL price)
        """
        try:
            # Validate minimum account size
            if len(account_data) < 232:
                print(f"[METEORA PARSE] ⚠ Account data too small ({len(account_data)} bytes), need at least 232 bytes")
                return None
            
            # Try bin_id parsing (fallback method)
            try:
                print(f"[METEORA PARSE] Attempting bin_id parsing from account data...")
                # Try common offset variations
                for base_dec_offset in [44, 50, 56]:
                    for quote_dec_offset in [45, 51, 57]:
                        for active_id_offset in [72, 80, 88, 96]:
                            for bin_step_offset in [76, 84, 92, 100]:
                                try:
                                    base_decimals = struct.unpack_from("<B", account_data, base_dec_offset)[0]
                                    quote_decimals = struct.unpack_from("<B", account_data, quote_dec_offset)[0]
                                    active_id = struct.unpack_from("<i", account_data, active_id_offset)[0]
                                    bin_step = struct.unpack_from("<H", account_data, bin_step_offset)[0]
                                    
                                    # Check if values look reasonable
                                    if 0 < bin_step <= 10000 and 0 < base_decimals <= 18 and 0 < quote_decimals <= 18:
                                        base = 1.0 + (bin_step / 10_000.0)
                                        raw_price = base ** active_id
                                        decimal_adjustment = 10 ** (base_decimals - quote_decimals)
                                        price = raw_price * decimal_adjustment
                                        
                                        if 1e-20 < price < 1e20:
                                            print(f"[METEORA PARSE] ✓ Found valid offsets: dec_off={base_dec_offset},{quote_dec_offset}, id_off={active_id_offset},{bin_step_offset}")
                                            print(f"[METEORA PARSE] ✓ price: ${price:.18f}")
                                            return price
                                except:
                                    pass
                
                print(f"[METEORA PARSE] ⚠ Could not find valid bin_id at any offset combination")
                return None
            
            except Exception as e:
                print(f"[METEORA PARSE] ✗ Error in bin_id parsing: {e}")
                return None
        
        except Exception as e:
            print(f"[METEORA PARSE] ✗ Error parsing Meteora pool: {e}")
            import traceback
            traceback.print_exc()
            return None

    def extract_pool_price_from_transaction(self, pool_id: str, signature: str) -> Optional[float]:
        """Extract Meteora pool price by:
        1. Fetching the pool creation transaction
        2. Extracting vault addresses from transaction
        3. Fetching actual vault balances from RPC
        4. Calculating price from balances

        This is MORE RELIABLE than parsing account data directly.
        Returns: quote token amount per 1 base token
        """
        try:
            print(f"[METEORA TX] Fetching price from pool address: {pool_id[:16]}...")

            # Always fetch the actual pool creation transaction from the pool address
            # (provided signature may not be pool creation, could be swaps, etc)
            try:
                sigs_response = requests.post(
                    self.rpc_http_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [pool_id, {"limit": 10}]
                    },
                    timeout=10
                ).json()

                if sigs_response.get("result") and sigs_response["result"]:
                    actual_signature = sigs_response["result"][-1]["signature"]  # Last sig is creation
                    print(f"[METEORA TX] ℹ Using pool creation signature: {actual_signature[:16]}...")
                else:
                    print(f"[METEORA TX] ⚠ Could not fetch signatures for pool, using provided signature")
                    actual_signature = signature
            except Exception as e:
                print(f"[METEORA TX] ⚠ Error fetching creation signature: {e}, using provided signature")
                actual_signature = signature

            # Get pool creation transaction
            response = requests.post(
                self.rpc_http_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        actual_signature,
                        {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0
                        }
                    ]
                },
                timeout=15
            )
            
            data = response.json()
            if "error" in data or not data.get("result"):
                print(f"[METEORA TX] ⚠ Transaction not found or error: {data.get('error', 'Unknown')}")
                return None
            
            tx_data = data["result"]
            
            # Get account keys
            account_keys = tx_data.get("transaction", {}).get("message", {}).get("accountKeys", [])
            if not account_keys:
                print(f"[METEORA TX] ⚠ No account keys in transaction")
                return None
            
            # Extract pubkeys (handle both dict and string formats)
            pubkeys = []
            for key in account_keys:
                if isinstance(key, dict):
                    pubkeys.append(key.get("pubkey", ""))
                else:
                    pubkeys.append(key)
            
            # Find SPL token accounts (vaults)
            token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            vaults = []

            # Check ALL accounts in the transaction for token accounts (not just first 10!)
            # Some transactions have vaults at higher indices
            for account in pubkeys:
                if not account or len(account) != 44:
                    continue

                try:
                    check_response = requests.post(
                        self.rpc_http_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getAccountInfo",
                            "params": [account, {"encoding": "base64"}]
                        },
                        timeout=5
                    )

                    check_data = check_response.json()
                    if check_data.get("result") and check_data["result"].get("value"):
                        owner = check_data["result"]["value"].get("owner", "")
                        if owner == token_program:
                            vaults.append(account)
                            print(f"[METEORA TX] ✓ Found vault: {account[:8]}...")
                except:
                    pass
            
            if len(vaults) < 2:
                print(f"[METEORA TX] ⚠ Found only {len(vaults)} vaults from provided signature, trying pool creation tx...")
                # Fallback: fetch actual pool creation transaction from pool address
                try:
                    sigs_response = requests.post(
                        self.rpc_http_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [pool_id, {"limit": 10}]
                        },
                        timeout=10
                    ).json()

                    if sigs_response.get("result") and sigs_response["result"]:
                        creation_sig = sigs_response["result"][-1]["signature"]  # Last sig is creation
                        print(f"[METEORA TX] ℹ Trying pool creation signature: {creation_sig[:16]}...")

                        # Retry transaction extraction with creation signature
                        response = requests.post(
                            self.rpc_http_url,
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [
                                    creation_sig,
                                    {
                                        "encoding": "jsonParsed",
                                        "maxSupportedTransactionVersion": 0
                                    }
                                ]
                            },
                            timeout=15
                        )

                        data = response.json()
                        if "error" not in data and data.get("result"):
                            tx_data = data["result"]
                            account_keys = tx_data.get("transaction", {}).get("message", {}).get("accountKeys", [])
                            pubkeys = []
                            for key in account_keys:
                                if isinstance(key, dict):
                                    pubkeys.append(key.get("pubkey", ""))
                                else:
                                    pubkeys.append(key)

                            # Extract vaults again with creation transaction
                            vaults = []
                            for account in pubkeys:
                                if not account or len(account) != 44:
                                    continue

                                try:
                                    check_response = requests.post(
                                        self.rpc_http_url,
                                        json={
                                            "jsonrpc": "2.0",
                                            "id": 1,
                                            "method": "getAccountInfo",
                                            "params": [account, {"encoding": "base64"}]
                                        },
                                        timeout=5
                                    )

                                    check_data = check_response.json()
                                    if check_data.get("result") and check_data["result"].get("value"):
                                        owner = check_data["result"]["value"].get("owner", "")
                                        if owner == token_program:
                                            vaults.append(account)
                                            print(f"[METEORA TX] ✓ Found vault: {account[:8]}...")
                                except:
                                    pass

                except Exception as e:
                    print(f"[METEORA TX] ⚠ Fallback to creation tx failed: {e}")

                if len(vaults) < 2:
                    print(f"[METEORA TX] ⚠ Still found only {len(vaults)} vaults after retrying")
                    return None
            
            # Get vault balances and mints - try ALL vaults, not just first 2
            token_vaults = []
            for vault in vaults:  # Try all vaults found
                try:
                    vault_info_response = requests.post(
                        self.rpc_http_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getAccountInfo",
                            "params": [vault, {"encoding": "base64"}]
                        },
                        timeout=5
                    )

                    vault_data = vault_info_response.json()
                    if not vault_data.get("result") or not vault_data["result"].get("value"):
                        continue

                    account_data_b64 = vault_data["result"]["value"].get("data", ["", ""])[0]
                    if not account_data_b64:
                        continue

                    account_data = base64.b64decode(account_data_b64)

                    if len(account_data) < 72:
                        continue

                    # Parse SPL token account
                    mint = base58.b58encode(account_data[0:32]).decode()
                    amount = struct.unpack("<Q", account_data[64:72])[0]

                    # Get decimals
                    decimals = self._get_mint_decimals(mint)
                    human_amount = amount / (10 ** (decimals or 6))

                    token_vaults.append({
                        "vault": vault,
                        "mint": mint,
                        "amount": amount,
                        "decimals": decimals or 6,
                        "human": human_amount
                    })

                    print(f"[METEORA TX] ✓ Vault {vault[:8]}... balance: {human_amount:.8f} ({mint[:8]}...)")

                except Exception as e:
                    print(f"[METEORA TX] ⚠ Error processing vault: {e}")
                    continue

            if len(token_vaults) < 2:
                print(f"[METEORA TX] ⚠ Could not get balances for {len(token_vaults)} vaults")
                return None

            # Filter out zero-balance vaults first
            token_vaults = [v for v in token_vaults if v["human"] > 0]

            if len(token_vaults) < 2:
                print(f"[METEORA TX] ⚠ After filtering zero balances, only {len(token_vaults)} vaults with balance > 0")
                return None

            # Keep only one vault per unique mint (the one with highest balance)
            # This handles cases where there are multiple positions of the same token
            mint_to_vault = {}
            for v in token_vaults:
                mint = v["mint"]
                if mint not in mint_to_vault or v["human"] > mint_to_vault[mint]["human"]:
                    mint_to_vault[mint] = v

            # Sort by balance and take top values for each mint
            token_vaults = sorted(mint_to_vault.values(), key=lambda v: v["human"], reverse=True)

            if len(token_vaults) < 2:
                print(f"[METEORA TX] ⚠ After deduplication by mint, only {len(token_vaults)} unique token types")
                return None

            print(f"[METEORA TX] ℹ Using {len(token_vaults)} vaults ({len(mint_to_vault)} unique tokens)")

            # Check for pool depletion (liquidity removal)
            # A pool is depleted if one vault is nearly empty while the other has balance
            balances = [v["human"] for v in token_vaults if v["human"] > 0]
            is_depleted = False
            depletion_reason = None

            if len(balances) < 2:
                print(f"[METEORA TX] ⚠ Pool appears depleted - less than 2 non-zero vaults")
                is_depleted = True
                depletion_reason = "Less than 2 non-zero vaults"
            else:
                min_balance = min(balances)
                max_balance = max(balances)

                # Depletion threshold: if smallest vault < 0.00001 (essentially dust)
                if min_balance < 0.00001:
                    print(f"[METEORA TX] ⚠ Pool appears depleted - smallest vault has only {min_balance:.18f}")
                    is_depleted = True
                    depletion_reason = f"Dust balance: {min_balance:.10f}"

                # Additional check: if smallest < 0.0001 and >100x smaller than largest
                elif min_balance < 0.0001 and max_balance > 0 and (max_balance / min_balance) > 100:
                    print(f"[METEORA TX] ⚠ Pool appears depleted - {max_balance/min_balance:.1f}x imbalance")
                    is_depleted = True
                    depletion_reason = f"Imbalance: {max_balance/min_balance:.1f}x"

            # Calculate best price from all vault pairs (matching V2 logic)
            # Priority: SOL pairs > token/token pairs, then by balance size
            sol_mint = "So11111111111111111111111111111111111111112"
            best_price = None
            best_pair = None
            best_is_sol_pair = False

            # Try all possible pairs
            for i in range(len(token_vaults)):
                for j in range(i + 1, len(token_vaults)):
                    vi, vj = token_vaults[i], token_vaults[j]
                    if vi["human"] <= 0 or vj["human"] <= 0:
                        continue

                    # Skip if both vaults hold the same token (no price ratio possible)
                    if vi["mint"] == vj["mint"]:
                        continue

                    # Try both directions: vj/vi (j as quote) and vi/vj (i as quote)
                    # Direction 1: vj as quote, vi as base (vj/vi)
                    price_vj_per_vi = vj["human"] / vi["human"]
                    is_vj_quote = vj["mint"] == sol_mint

                    should_use_vj = False
                    if best_price is None:
                        should_use_vj = True
                    elif is_vj_quote and not best_is_sol_pair:
                        # Prefer SOL pairs
                        should_use_vj = True
                    elif is_vj_quote == best_is_sol_pair:
                        # Both same type - prefer larger base balance (better liquidity)
                        best_base_balance = best_pair[1]["human"]
                        if vi["human"] > best_base_balance * 1.1:  # 10% threshold
                            should_use_vj = True

                    if should_use_vj:
                        best_price = price_vj_per_vi
                        best_pair = (vi, vj)
                        best_is_sol_pair = is_vj_quote

                    # Direction 2: vi as quote, vj as base (vi/vj)
                    price_vi_per_vj = vi["human"] / vj["human"]
                    is_vi_quote = vi["mint"] == sol_mint

                    should_use_vi = False
                    if best_price is None:
                        should_use_vi = True
                    elif is_vi_quote and not best_is_sol_pair:
                        # Prefer SOL pairs
                        should_use_vi = True
                    elif is_vi_quote == best_is_sol_pair:
                        # Both same type - prefer larger base balance
                        best_base_balance = best_pair[1]["human"]
                        if vj["human"] > best_base_balance * 1.1:  # 10% threshold
                            should_use_vi = True

                    if should_use_vi:
                        best_price = price_vi_per_vj
                        best_pair = (vj, vi)
                        best_is_sol_pair = is_vi_quote

            if best_price is not None and best_pair is not None:
                base, quote = best_pair
                if is_depleted:
                    print(f"[METEORA TX] ⚠ Depleted pool but price: {quote['mint'][:8]}... / {base['mint'][:8]}... = {best_price:.18f} (Reason: {depletion_reason})")
                else:
                    print(f"[METEORA TX] ✓ Best pair: {quote['mint'][:8]}... / {base['mint'][:8]}... = {best_price:.18f}")

                # Return dict with price and depletion status
                return {
                    'price': best_price,
                    'is_depleted': is_depleted,
                    'depletion_reason': depletion_reason
                }

            # Fallback: use first pair if algo didn't select
            price = token_vaults[1]["human"] / token_vaults[0]["human"] if token_vaults[0]["human"] > 0 else 0
            if is_depleted:
                print(f"[METEORA TX] ⚠ Depleted pool fallback: {token_vaults[1]['human']:.8f} / {token_vaults[0]['human']:.8f} = ${price:.18f}")
            else:
                print(f"[METEORA TX] ✓ Fallback price: {token_vaults[1]['human']:.8f} / {token_vaults[0]['human']:.8f} = ${price:.18f}")

            return {
                'price': price,
                'is_depleted': is_depleted,
                'depletion_reason': depletion_reason
            }
        
        except Exception as e:
            print(f"[METEORA TX] ✗ Error extracting price from transaction: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def subscribe_to_program(self, ws, program_id: str):
        """Subscribe to a Raydium program for new transactions"""
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [program_id]},
                {"commitment": "confirmed"}
            ]
        }
        await ws.send(json.dumps(subscribe_msg))
        response = await ws.recv()
        result = json.loads(response)
        print(f"Subscribed to {program_id[:8]}... : {result}")
        return result

    def is_pool_creation(self, logs: List[str]) -> bool:
        """Check if logs indicate a new pool creation (not swaps/deposits/add liquidity)"""
        logs_text = ' '.join(logs)

        # Exclude non-creation operations
        exclude_patterns = [
            'swap', 'route', 'Swap', 'Route',
            'process_swap', 'swap_base',
            'routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS',  # Raydium router
            'deposit', 'Deposit',
            'withdraw', 'Withdraw',
            'harvest', 'Harvest',
            'liquidity', 'Liquidity',
            'extend_account',  # Token account extension
            'collect', 'Collect',  # Fee collection operations
        ]
        if any(pattern in logs_text for pattern in exclude_patterns):
            return False

        # Check for Raydium V4 pool creation - use initialize2 instruction
        if f'Program {self.RAYDIUM_V4_PROGRAM} invoke [1]' in logs_text:
            # Raydium V4 uses 'initialize2' for new pool creation
            has_initialize2 = 'initialize2' in logs_text.lower()
            return has_initialize2
        
        # Check for Raydium CPMM pool creation - check for specific instruction right after invoke
        if f'Program {self.RAYDIUM_CPMM_PROGRAM} invoke [1]' in logs_text:
            # Look for the instruction log immediately after invoke [1]
            # CPMM pool creation uses: Instruction: InitializeWithPermission
            # Must be the MAIN instruction (not InitializeImmutableOwner or InitializeAccount3)
            cpmm_pool_creation = (
                'Instruction: InitializeWithPermission' in logs_text or
                'Instruction: Initialize2' in logs_text
            )
            return cpmm_pool_creation
        
        # Check for Meteora pool creation (standard DLMM)
        if f'Program {self.METEORA_PROGRAM} invoke [1]' in logs_text:
            # Meteora DLMM on-chain instruction discriminators (NOT SDK function names)
            # These are the actual instruction types sent to Solana
            meteora_instructions = [
                'initialize_customizable_permissionless_lb_pair',
                'initialize_customizable_permissionless_lb_pair2',
                'initialize_lb_pair',
                'initialize_lb_pair2',
                'initialize_permission_lb_pair',
                'migration_damm_v2',  # Meteora migration instruction
            ]
            return any(instr in logs_text.lower() for instr in meteora_instructions)

        # Check for Meteora pool creation (alternative program variant)
        if f'Program {self.METEORA_ALT_PROGRAM} invoke' in logs_text:
            # Alternative Meteora program uses different instruction names
            return 'InitializePoolWithDynamicConfig' in logs_text

        return False

    def get_dex_source(self, logs: List[str]) -> str:
        """Determine which DEX the pool is from based on transaction logs"""
        logs_text = ' '.join(logs)
        
        if f'Program {self.RAYDIUM_V4_PROGRAM}' in logs_text:
            return 'Raydium V4'
        elif f'Program {self.RAYDIUM_CPMM_PROGRAM}' in logs_text:
            return 'Raydium CPMM'
        elif f'Program {self.METEORA_PROGRAM}' in logs_text:
            return 'Meteora'
        elif f'Program {self.METEORA_ALT_PROGRAM}' in logs_text:
            return 'Meteora'
        else:
            return 'Unknown'

    async def listen_for_pools(self):
        """Listen for new pool creation events from Raydium (V4 & CPMM) and Meteora

        Filters for specific pool creation instructions (on-chain discriminators):
        - Raydium V4: 'initialize2' instruction
        - Raydium CPMM: 'InitializeWithPermission' or 'Initialize' instruction
        - Meteora DLMM (standard): 'initialize_customizable_permissionless_lb_pair',
          'initialize_customizable_permissionless_lb_pair2', 'initialize_lb_pair',
          'initialize_lb_pair2', 'initialize_permission_lb_pair', or
          'migration_damm_v2' (pool migration) instructions
        - Meteora (alternative): 'InitializePoolWithDynamicConfig' instruction

        Note: Uses actual on-chain instruction discriminators, NOT SDK function names.
        SDK functions like createLbPair map to on-chain initialize_* instruction types.

        Excludes:
        - Swaps, routes, deposits, withdrawals, harvests, fee collection
        """
        print(f"Connecting to Solana WebSocket: {self.rpc_ws_url}")

        while self.is_running:
            try:
                async with websockets.connect(self.rpc_ws_url) as ws:
                    # Subscribe to Raydium V4, Raydium CPMM, and Meteora programs
                    await self.subscribe_to_program(ws, self.RAYDIUM_V4_PROGRAM)
                    await self.subscribe_to_program(ws, self.RAYDIUM_CPMM_PROGRAM)
                    await self.subscribe_to_program(ws, self.METEORA_PROGRAM)
                    await self.subscribe_to_program(ws, self.METEORA_ALT_PROGRAM)

                    print("Listening for new pool launches from Raydium (V4 & CPMM) and Meteora...")
                    print("- Raydium V4: Filtering for 'initialize2' instruction")
                    print("- Raydium CPMM: Filtering for 'InitializeWithPermission' or 'Initialize' instruction")
                    print("- Meteora (standard): Filtering for on-chain initialize_* and migration_damm_v2 instructions")
                    print("- Meteora (alternative): Filtering for 'InitializePoolWithDynamicConfig' instruction")

                    while self.is_running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            data = json.loads(msg)

                            if 'params' in data and 'result' in data['params']:
                                result = data['params']['result']
                                logs = result.get('value', {}).get('logs', [])
                                signature = result.get('value', {}).get('signature', '')
                                err = result.get('value', {}).get('err')

                                # Skip failed transactions
                                if err:
                                    continue

                                # Determine which DEX this is from
                                dex_source = self.get_dex_source(logs)

                                # Only process actual pool creations (not swaps, deposits, etc)
                                if signature and self.is_pool_creation(logs):
                                    # Check if we're paused from processing new pools
                                    # Skip entire processing if paused
                                    with pause_lock:
                                        if pause_new_pools:
                                            print(f"[PAUSE] ⏸ Ignoring new {dex_source} pool (pause mode): {signature}")
                                            continue

                                    print(f"\n{'='*50}")
                                    print(f"New {dex_source} pool launch: {signature}")

                                    pool_data = self.parse_pool_from_logs(logs, signature, dex_source)
                                    pool_data['dex'] = dex_source

                                    # Log token address and symbol
                                    print(f"Token Address: {pool_data.get('baseMint', 'Unknown')}")
                                    print(f"Token Symbol: {pool_data.get('symbol', 'Unknown')}")
                                    print(f"Token Name: {pool_data.get('name', 'Unknown')}")
                                    print(f"DEX: {dex_source}")
                                    print(f"{'='*50}\n")

                                    if not self.db.pool_exists(pool_data['ammId']):
                                        if self.db.insert_pool(pool_data):
                                            print(f"Stored new {dex_source} pool: {pool_data['ammId']}")

                                            # Initialize depletion tracking variables
                                            is_depleted = False
                                            depletion_reason = None

                                            # Fetch initial price and supply for new pool
                                            if pool_data.get('baseMint'):
                                                print(f"[PRICE INIT] Fetching initial price and supply for {pool_data['ammId'][:8]}...")
                                                price_result = self.fetch_pool_price(pool_data['ammId'], pool_data['baseMint'], signature)
                                                if price_result and price_result.get('price') is not None:
                                                    initial_price = price_result['price']
                                                    is_depleted = price_result.get('is_depleted', False)
                                                    depletion_reason = price_result.get('depletion_reason')

                                                    # Also fetch total supply
                                                    total_supply = self.get_token_total_supply(pool_data['baseMint'])

                                                    if total_supply is not None:
                                                        market_cap = total_supply * initial_price
                                                        self.db.update_pool_supply_and_price(pool_data['ammId'], total_supply, initial_price, is_initial=True)
                                                        if is_depleted:
                                                            self.db.update_depletion_status(pool_data['ammId'], is_depleted, depletion_reason)
                                                        print(f"[PRICE INIT] ✓ Initial price set: ${initial_price:.8f}")
                                                        print(f"[PRICE INIT] ✓ Total supply: {total_supply:,.0f}")
                                                        print(f"[PRICE INIT] ✓ Market cap: ${market_cap:,.0f}")
                                                        if is_depleted:
                                                            print(f"[PRICE INIT] ⚠ Pool is depleted: {depletion_reason}")
                                                    else:
                                                        # Just update price if supply fetch fails
                                                        self.db.update_pool_price(pool_data['ammId'], initial_price, is_initial=True)
                                                        if is_depleted:
                                                            self.db.update_depletion_status(pool_data['ammId'], is_depleted, depletion_reason)
                                                        print(f"[PRICE INIT] ✓ Initial price set: ${initial_price:.8f}")
                                                        print(f"[PRICE INIT] ⚠ Could not fetch total supply")
                                                        if is_depleted:
                                                            print(f"[PRICE INIT] ⚠ Pool is depleted: {depletion_reason}")
                                                else:
                                                    print(f"[PRICE INIT] ⚠ Could not fetch initial price")

                                            # Also fetch DexScreener price for initial setup
                                            dex_data = None
                                            sol_usd_price = None
                                            if pool_data.get('baseMint'):
                                                dex_data = self.get_dexscreener_price(pool_data['baseMint'])
                                                if dex_data and dex_data.get('priceUsd'):
                                                    self.db.update_dexscreener_price(pool_data['ammId'], dex_data['priceUsd'], dex_data.get('priceNative'))
                                                    print(f"[DEXSCREENER INIT] ✓ Initial DexScreener price: ${dex_data['priceUsd']:.10f}")

                                                    # Calculate SOL/USD rate from DexScreener data for UI price conversion
                                                    if dex_data.get('priceNative') and dex_data.get('priceNative') > 0:
                                                        sol_usd_price = dex_data['priceUsd'] / dex_data['priceNative']
                                                        print(f"[DEXSCREENER INIT] ✓ Calculated SOL/USD rate: {sol_usd_price:.8f}")

                                            # Broadcast new pool to UI immediately with snake_case field names
                                            broadcast_data = {
                                                'amm_id': pool_data.get('ammId'),
                                                'name': pool_data.get('name', 'Unknown'),
                                                'symbol': pool_data.get('symbol', ''),
                                                'image': pool_data.get('image', ''),
                                                'base_mint': pool_data.get('baseMint'),
                                                'liquidity': pool_data.get('liquidity', 0),
                                                'price': pool_data.get('price', 0),
                                                'signature': pool_data.get('signature'),
                                                'dex': dex_source,
                                                'first_seen': pool_data.get('first_seen'),  # Use detection time, not broadcast time
                                                'creation_price': initial_price if initial_price is not None else 0,
                                                'current_price': initial_price if initial_price is not None else 0,
                                                'is_depleted': is_depleted,
                                                'depletion_reason': depletion_reason,
                                                'sol_usd_price': sol_usd_price  # Include SOL/USD rate for UI price conversion
                                            }
                                            print(f"[BROADCAST] Adding {broadcast_data['name']} ({broadcast_data['symbol']}) to queue. Queue size before: {pool_broadcast_queue.qsize()}")
                                            if broadcast_data['image']:
                                                print(f"[BROADCAST] ✓ Image URL present: {broadcast_data['image']}")
                                            else:
                                                print(f"[BROADCAST] ✗ WARNING: No image URL for {broadcast_data['name']} - metadata fetch may have failed")
                                            pool_broadcast_queue.put(broadcast_data)
                                            print(f"[BROADCAST] Queue size after: {pool_broadcast_queue.qsize()}")

                        except asyncio.TimeoutError:
                            # Send ping to keep connection alive
                            await ws.ping()

            except Exception as e:
                print(f"WebSocket error: {e}")
                if self.is_running:
                    print("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    def run_websocket_loop(self):
        """Run the async WebSocket listener in a thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.listen_for_pools())

    def start_background_monitor(self):
        """Start WebSocket monitoring in background thread"""
        self.is_running = True
        thread = Thread(target=self.run_websocket_loop, daemon=True)
        thread.start()
        print("WebSocket monitor started")
        return thread

    def update_pool_prices(self):
        """Background thread that updates pool prices on a sliding scale based on age"""
        print("[PRICE UPDATER] Price update thread started")
        update_cycle = 0

        while self.is_running:
            try:
                update_cycle += 1
                print(f"\n[PRICE UPDATER] === Cycle {update_cycle} ===")

                # Get pools that need updating
                pools_to_update = self.db.get_pools_needing_update()

                if pools_to_update:
                    print(f"[PRICE UPDATER] Found {len(pools_to_update)} pool(s) needing update")
                    ages_str = ", ".join([f"{p['age_seconds']:.0f}s" for p in pools_to_update])
                    print(f"[PRICE UPDATER] Pool ages: {ages_str}")
                else:
                    print(f"[PRICE UPDATER] No pools need updating at this time")

                for i, pool_info in enumerate(pools_to_update, 1):
                    if not self.is_running:
                        break

                    amm_id = pool_info['amm_id']
                    base_mint = pool_info['base_mint']
                    age_seconds = pool_info['age_seconds']
                    signature = pool_info.get('signature')  # Get stored signature

                    # Determine update interval
                    if age_seconds < 300:
                        interval_str = "30s (0-5min)"
                    elif age_seconds < 1800:
                        interval_str = "2min (5-30min)"
                    else:
                        interval_str = "5min (30+min)"

                    print(f"[PRICE UPDATER] [{i}/{len(pools_to_update)}] Pool age: {age_seconds:.0f}s, interval: {interval_str}")

                    # Fetch current price from blockchain (use signature if available)
                    price_result = self.fetch_pool_price(amm_id, base_mint, signature)

                    if price_result and price_result.get('price') is not None:
                        current_price = price_result['price']
                        is_depleted = price_result.get('is_depleted', False)
                        depletion_reason = price_result.get('depletion_reason')

                        # Fetch total supply
                        total_supply = self.get_token_total_supply(base_mint)

                        if total_supply is not None:
                            # Update in database with supply and price
                            self.db.update_pool_supply_and_price(amm_id, total_supply, current_price)
                            if is_depleted:
                                self.db.update_depletion_status(amm_id, is_depleted, depletion_reason)
                            market_cap = total_supply * current_price
                            print(f"[PRICE UPDATER] ✓ Updated {base_mint[:8]}...: ${current_price:.8f} (supply: {total_supply:,.0f}, mcap: ${market_cap:,.0f})")
                            if is_depleted:
                                print(f"[PRICE UPDATER] ⚠ Pool is depleted: {depletion_reason}")
                        else:
                            # Update price only if supply fetch fails
                            self.db.update_pool_price(amm_id, current_price)
                            if is_depleted:
                                self.db.update_depletion_status(amm_id, is_depleted, depletion_reason)
                            print(f"[PRICE UPDATER] ✓ Updated price for {base_mint[:8]}...: ${current_price:.8f}")
                            if is_depleted:
                                print(f"[PRICE UPDATER] ⚠ Pool is depleted: {depletion_reason}")
                    else:
                        print(f"[PRICE UPDATER] ✗ Could not fetch price for {base_mint[:8]}...")

                    # Also fetch DexScreener price for comparison
                    dex_data = self.get_dexscreener_price(base_mint)
                    if dex_data and dex_data.get('priceUsd'):
                        self.db.update_dexscreener_price(amm_id, dex_data['priceUsd'], dex_data.get('priceNative'))
                        print(f"[DEXSCREENER] Updated for {base_mint[:8]}...: ${dex_data['priceUsd']:.10f}")

                    # Rate limiting - small delay between updates
                    time.sleep(0.5)

                # Check again after 10 seconds
                print(f"[PRICE UPDATER] Cycle {update_cycle} complete, sleeping 10s before next cycle...")
                time.sleep(10)

            except Exception as e:
                print(f"[PRICE UPDATER] ✗ Error in price update loop: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)

    def start_price_updater(self):
        """Start background price update thread"""
        thread = Thread(target=self.update_pool_prices, daemon=True)
        thread.start()
        print("[PRICE UPDATER] Price updater thread started")
        return thread


# Flask Web Application
app = Flask(__name__)
monitor = RaydiumMonitor()

# Global queue for real-time pool broadcasts
pool_broadcast_queue = queue.Queue()

# Global pause state for new pool listening
pause_new_pools = False
pause_lock = threading.Lock()

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Raydium Launchlab Monitor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
            background: #0a0e27;
            color: #e0e8f0;
            min-height: 100vh;
        }

        .top-bar {
            background: #0f1429;
            border-bottom: 1px solid #1a2847;
            padding: 12px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .logo {
            font-size: 20px;
            font-weight: bold;
            color: #fff;
        }

        .logo-accent {
            color: #ffd700;
        }

        .controls {
            display: flex;
            gap: 12px;
            align-items: center;
        }

        .toggle-btn {
            background: #1a2847;
            border: 1px solid #ffd700;
            color: #ffd700;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            transition: all 0.3s;
        }

        .toggle-btn:hover {
            background: #ffd700;
            color: #0a0e27;
        }

        .toggle-btn.paused {
            background: #ff6b6b;
            border-color: #ff6b6b;
            color: #fff;
        }

        .toggle-btn.paused:hover {
            background: #ff5252;
        }

        .status-indicator {
            font-size: 12px;
            color: #8892b0;
            padding: 0 8px;
        }

        .status-indicator.active {
            color: #4ade80;
        }

        .status-indicator.paused {
            color: #ff6b6b;
        }

        .nav-tabs {
            display: flex;
            gap: 30px;
            font-size: 14px;
        }

        .nav-tabs a {
            color: #8892b0;
            text-decoration: none;
            cursor: pointer;
            transition: color 0.3s;
        }

        .nav-tabs a:hover,
        .nav-tabs a.active {
            color: #fff;
        }

        .nav-tabs a.active {
            border-bottom: 2px solid #ffd700;
            padding-bottom: 12px;
            margin-bottom: -12px;
        }

        .container {
            display: grid;
            grid-template-columns: 1fr 350px;
            gap: 20px;
            padding: 20px;
            max-width: 1600px;
            margin: 0 auto;
        }

        .main-content {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .chart-section {
            background: #111729;
            border: 1px solid #1a2847;
            border-radius: 12px;
            padding: 20px;
            min-height: 400px;
        }

        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }

        .chart-title {
            font-size: 16px;
            font-weight: 600;
            color: #fff;
        }

        .chart-controls {
            display: flex;
            gap: 10px;
        }

        .chart-controls button {
            background: transparent;
            border: 1px solid #1a2847;
            color: #8892b0;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.3s;
        }

        .chart-controls button:hover {
            border-color: #2a3847;
            color: #fff;
        }

        .chart-placeholder {
            height: 350px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #8892b0;
            font-size: 14px;
        }

        .pools-section {
            background: #111729;
            border: 1px solid #1a2847;
            border-radius: 12px;
            padding: 20px;
        }

        .section-title {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 15px;
            color: #fff;
        }

        .pools-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .pool-item {
            background: #0a0e27;
            border: 1px solid #1a2847;
            border-radius: 8px;
            padding: 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: all 0.3s;
        }

        .pool-item:hover {
            border-color: #2a4a7f;
            background: #0f1735;
        }

        .pool-left {
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
        }

        .pool-icon {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: #fff;
            flex-shrink: 0;
        }

        .pool-icon.has-image {
            font-size: 0;
            background: #1a2847;
            /* Remove gradient background while image loads */
        }

        .pool-icon.has-image img {
            animation: fadeIn 0.3s ease-in;
        }

        @keyframes fadeIn {
            from {
                opacity: 0;
            }
            to {
                opacity: 1;
            }
        }

        .pool-info {
            flex: 1;
        }

        .pool-name {
            font-size: 13px;
            font-weight: 600;
            color: #fff;
        }

        .pool-address {
            font-size: 11px;
            color: #8892b0;
            font-family: monospace;
            margin-top: 2px;
        }

        .pool-right {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 4px;
        }

        .pool-time {
            font-size: 11px;
            color: #8892b0;
        }

        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .sidebar-card {
            background: #111729;
            border: 1px solid #1a2847;
            border-radius: 12px;
            padding: 16px;
        }

        .card-label {
            font-size: 12px;
            color: #8892b0;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .card-value {
            font-size: 24px;
            font-weight: bold;
            color: #fff;
            margin-bottom: 8px;
        }

        .card-subvalue {
            font-size: 12px;
            color: #8892b0;
        }

        .token-select {
            background: #0a0e27;
            border: 1px solid #1a2847;
            border-radius: 8px;
            padding: 12px;
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
        }

        .token-select:hover {
            border-color: #2a4a7f;
        }

        .token-select-content {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .token-select-icon {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #667eea;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
        }

        .token-select-text {
            display: flex;
            flex-direction: column;
        }

        .token-select-name {
            font-weight: 600;
            font-size: 14px;
        }

        .token-select-amount {
            font-size: 11px;
            color: #8892b0;
        }

        .action-button {
            background: linear-gradient(135deg, #00d084 0%, #00a066 100%);
            border: none;
            color: #fff;
            padding: 12px 20px;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            transition: all 0.3s;
            font-size: 14px;
        }

        .action-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0, 208, 132, 0.3);
        }

        .loading {
            text-align: center;
            color: #8892b0;
            font-size: 14px;
            padding: 40px;
        }

        .refresh-badge {
            background: #1a2847;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: #8892b0;
            margin-left: 8px;
        }

        .badge-depleted {
            background: #ff6b6b;
            color: #fff;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 10px;
            margin-left: 8px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }

        .badge-depleted::before {
            content: '⚠';
            font-size: 11px;
        }

        @media (max-width: 1024px) {
            .container {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="top-bar">
        <div class="logo">Raydium <span class="logo-accent">Launchlab</span></div>
        <div class="nav-tabs">
            <a class="active">Monitor</a>
            <a>Portfolio</a>
            <a>Analytics</a>
            <a>Settings</a>
        </div>
        <div class="controls">
            <span class="status-indicator active" id="statusIndicator">● Live</span>
            <button class="toggle-btn" id="pauseBtn">Pause New Pools</button>
        </div>
    </div>

    <div class="container">
        <div class="main-content">
            <div class="chart-section">
                <div class="chart-header">
                    <div class="chart-title">New Pool Launches</div>
                    <div class="chart-controls">
                        <button>15m</button>
                        <button>1h</button>
                        <button class="active">24h</button>
                        <button>1w</button>
                    </div>
                </div>
                <div class="chart-placeholder">
                    📊 Chart data will be displayed here
                </div>
            </div>

            <div class="pools-section">
                <div class="section-title">Latest Pools <span class="refresh-badge">Auto-refresh: 30s</span></div>
                <div class="pools-list" id="poolsContainer">
                    <div class="loading">Loading pools...</div>
                </div>
            </div>
        </div>

        <div class="sidebar">
            <div class="sidebar-card">
                <div class="card-label">Total Pools</div>
                <div class="card-value" id="totalPools">-</div>
                <div class="card-subvalue">All time</div>
            </div>

            <div class="sidebar-card">
                <div class="card-label">New Today</div>
                <div class="card-value" id="newToday">-</div>
                <div class="card-subvalue">Last 24 hours</div>
            </div>

            <div class="sidebar-card">
                <div class="card-label">Total Liquidity</div>
                <div class="card-value" id="totalLiquidity">-</div>
                <div class="card-subvalue">Across all pools</div>
            </div>

            <div class="token-select">
                <div class="token-select-content">
                    <div class="token-select-icon">✨</div>
                    <div class="token-select-text">
                        <div class="token-select-name">SOL</div>
                        <div class="token-select-amount">-$0</div>
                    </div>
                </div>
                <div>⌄</div>
            </div>

            <button class="action-button">Buy New Token</button>
        </div>
    </div>

    <script>
        // Global state for pause/resume
        let isPaused = false;

        // Setup pause button handler
        document.addEventListener('DOMContentLoaded', function() {
            const pauseBtn = document.getElementById('pauseBtn');
            const statusIndicator = document.getElementById('statusIndicator');

            // Get initial pause state from server
            fetch('/api/pause-status')
                .then(response => response.json())
                .then(data => {
                    isPaused = data.paused;
                    updatePauseUI();
                });

            if (pauseBtn) {
                pauseBtn.addEventListener('click', function() {
                    // Send pause toggle request to server
                    fetch('/api/pause', { method: 'POST' })
                        .then(response => response.json())
                        .then(data => {
                            isPaused = data.paused;
                            console.log(`[PAUSE] Server response: ${data.message}`);
                            updatePauseUI();
                        })
                        .catch(error => {
                            console.error('[PAUSE] Error toggling pause state:', error);
                        });
                });
            }

            function updatePauseUI() {
                if (isPaused) {
                    pauseBtn.textContent = 'Resume New Pools';
                    pauseBtn.classList.add('paused');
                    statusIndicator.textContent = '● Paused';
                    statusIndicator.classList.remove('active');
                    statusIndicator.classList.add('paused');
                    console.log('[PAUSE] UI updated: New pool additions paused - only price updates');
                } else {
                    pauseBtn.textContent = 'Pause New Pools';
                    pauseBtn.classList.remove('paused');
                    statusIndicator.textContent = '● Live';
                    statusIndicator.classList.add('active');
                    statusIndicator.classList.remove('paused');
                    console.log('[PAUSE] UI updated: New pool additions resumed');
                }
            }
        });

        function formatNumber(num) {
            if (num >= 1000000) {
                return '$' + (num / 1000000).toFixed(2) + 'M';
            } else if (num >= 1000) {
                return '$' + (num / 1000).toFixed(2) + 'K';
            }
            return '$' + num.toFixed(2);
        }

        function formatPrice(price) {
            if (!price || price === 0) return '-';
            if (price < 0.01) {
                return '$' + price.toFixed(8);
            } else if (price < 1) {
                return '$' + price.toFixed(6);
            }
            return '$' + price.toFixed(4);
        }

        function formatActiveTime(firstSeen) {
            const poolTime = new Date(firstSeen);
            const now = new Date();
            const diffMs = now - poolTime;
            const diffSecs = Math.floor(diffMs / 1000);
            const diffMins = Math.floor(diffSecs / 60);
            const diffHours = Math.floor(diffMins / 60);
            const diffDays = Math.floor(diffHours / 24);

            if (diffDays > 0) {
                return `${diffDays}d ${diffHours % 24}h`;
            } else if (diffHours > 0) {
                return `${diffHours}h ${diffMins % 60}m`;
            } else if (diffMins > 0) {
                return `${diffMins}m ${diffSecs % 60}s`;
            } else {
                return `${diffSecs}s ago`;
            }
        }

        // Update all pool times every second
        function updateAllPoolTimes() {
            const poolTimes = document.querySelectorAll('[data-first-seen]');
            if (poolTimes.length > 0) {
                console.log(`[TIME] Updating ${poolTimes.length} pool time(s)`);
                poolTimes.forEach(el => {
                    const firstSeen = el.dataset.firstSeen;
                    const newTime = formatActiveTime(firstSeen);
                    if (el.textContent !== newTime) {
                        console.log(`[TIME] Updated: ${el.textContent} → ${newTime}`);
                        el.textContent = newTime;
                    }
                });
            }
        }

        // Fetch Meteora price data for a pool
        async function fetchMeteoraPriceData(tokenMint) {
            const priceDataDiv = document.getElementById(`price-data-${tokenMint}`);

            if (!priceDataDiv) return;

            priceDataDiv.textContent = 'Fetching price data...';

            try {
                const response = await fetch(`/api/meteora/price/${tokenMint}`);
                const data = await response.json();

                if (!response.ok) {
                    const errorMsg = `❌ ${data.error || 'Failed to fetch price'}`;
                    priceDataDiv.textContent = errorMsg;
                    console.log(`[PRICE] Failed to fetch price for ${tokenMint}: ${data.error}`);
                    return;
                }

                // Log to console
                console.log(`[PRICE] ✓ Fetched on-chain price for ${tokenMint}`, {
                    on_chain_price: data.on_chain_price,
                    total_supply: data.total_supply,
                    market_cap: data.market_cap
                });

                let html = '';

                // On-chain price display
                // Always display price if it's a valid number (not null, undefined, or 0)
                if (data.on_chain_price !== null && data.on_chain_price !== undefined && data.on_chain_price !== 0) {
                    // Use pre-calculated USD price if available, otherwise calculate from SOL rate
                    let onChainUsd = data.on_chain_price_usd;
                    if (!onChainUsd) {
                        // Fallback: Calculate SOL/USD rate from DexScreener data if available
                        if (data.dexscreener_data && data.dexscreener_data.priceNative && data.dexscreener_data.priceUsd) {
                            const solUsdRate = data.dexscreener_data.priceUsd / data.dexscreener_data.priceNative;
                            onChainUsd = data.on_chain_price * solUsdRate;
                        }
                    }

                    const onChainDisplay = onChainUsd ? formatPrice(onChainUsd) : (data.on_chain_price ? `${data.on_chain_price.toFixed(10)} SOL` : 'N/A');

                    html += `<div style="background: #1a2847; padding: 8px; border-radius: 4px; text-align: center;">
                        <div style="font-size: 8px; color: #888; margin-bottom: 3px;">💹 ON-CHAIN PRICE</div>
                        <div style="font-size: 12px; color: #00d4ff; font-weight: bold; word-break: break-all;">${onChainDisplay}</div>
                        <div style="font-size: 9px; color: #0088ff; margin-top: 3px;">Supply: ${data.total_supply ? (data.total_supply / 1e9).toFixed(2) + 'B' : 'N/A'}</div>
                    </div>`;

                    // Add market cap if available
                    if (data.market_cap) {
                        const mcapDisplay = data.market_cap < 1000 ? `$${data.market_cap.toFixed(2)}` : `$${(data.market_cap / 1000).toFixed(1)}k`;
                        html += `<div style="font-size: 9px; color: #aaa; margin-top: 8px; padding: 4px; background: #0f1429; border-radius: 3px;">Market Cap: ${mcapDisplay}</div>`;
                    }
                } else {
                    html = '<div style="color: #aaa; font-size: 9px;">No on-chain price data available</div>';
                }

                priceDataDiv.innerHTML = html;

            } catch (error) {
                priceDataDiv.textContent = `❌ Error: ${error.message}`;
                console.error(`[PRICE] Error fetching price for ${tokenMint}:`, error);
            }
        }

        // Track which pools have been rendered to prevent duplicates
        const renderedPoolMints = new Set();

        function getInitials(name) {
            if (!name || name === 'Unknown') return '?';
            return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
        }

        function renderPool(pool) {
            const initials = getInitials(pool.name);
            const shortAddress = pool.base_mint ? pool.base_mint.slice(0, 8) + '...' + pool.base_mint.slice(-6) : 'Unknown';
            // Use real price change from server, or 0 if not available yet
            const changePercent = pool.price_change_percent !== undefined ? Math.round(pool.price_change_percent * 100) / 100 : 0;
            const changeClass = changePercent >= 0 ? '' : 'negative';
            const dexBadge = pool.dex ? `<span style="background: #1a2847; padding: 2px 8px; border-radius: 4px; font-size: 10px; margin-left: 8px; color: #ffd700;">${pool.dex}</span>` : '';
            const depletedBadge = pool.is_depleted ? `<span class="badge-depleted" title="Liquidity has drained">Depleted</span>` : '';

            // Convert on-chain price to USD if SOL rate available
            let displayPrice = pool.current_price;
            if (pool.sol_usd_price && pool.sol_usd_price > 0) {
                displayPrice = pool.current_price * pool.sol_usd_price;
            }

            // Build pool icon: use image if available, otherwise use initials with gradient
            let iconHTML = '';
            if (pool.image && pool.image.trim()) {
                // Image available - embed directly as <img> tag
                console.log(`[RENDER] Pool: ${pool.name}, Image URL: ${pool.image}`);
                const safeImageUrl = pool.image.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
                iconHTML = `<div class="pool-icon has-image" id="icon-${pool.base_mint}"><img src="${safeImageUrl}" alt="${pool.name}" style="width:100%; height:100%; object-fit: cover; display: block;" /></div>`;
            } else {
                // No image - use initials with gradient background
                console.log(`[RENDER] Pool: ${pool.name}, No image URL`);
                iconHTML = `<div class="pool-icon">${initials}</div>`;
            }

            return `
                <div class="pool-item" id="pool-${pool.base_mint}">
                    <div class="pool-left">
                        ${iconHTML}
                        <div class="pool-info">
                            <div class="pool-name" style="display: flex; align-items: center;">${pool.name || 'Unknown'} ${pool.symbol ? '(' + pool.symbol + ')' : ''} ${dexBadge} ${depletedBadge}</div>
                            <div class="pool-address">
                                <a href="https://solscan.io/token/${pool.base_mint}" target="_blank" style="color: #8892b0; text-decoration: none;">${shortAddress}</a>
                            </div>
                        </div>
                    </div>
                    <div class="pool-right">
                        <div class="pool-time" data-first-seen="${pool.first_seen}">${formatActiveTime(pool.first_seen)}</div>
                        <div id="price-data-${pool.base_mint}" style="font-size: 11px; margin-top: 8px; padding: 8px; background: transparent; border: none; border-radius: 4px;"></div>
                    </div>
                </div>
            `;
        }

        // DISABLED: updateStats() was causing images to disappear
        // Stats are not updated - focus is on stability over metrics
        // function updateStats(data) { ... }

        // DISABLED: updatePools() was causing images to disappear
        // We rely entirely on /api/pools/new polling which never modifies existing DOM
        // function updatePools() { ... }

        function addNewPoolToUI(pool) {
            console.log(`[RENDER] addNewPoolToUI called for: ${pool.name}`);

            // Skip if this pool was already rendered
            if (renderedPoolMints.has(pool.base_mint)) {
                console.log(`[RENDER] Pool ${pool.name} already rendered, skipping duplicate`);
                return;
            }
            renderedPoolMints.add(pool.base_mint);

            const container = document.getElementById('poolsContainer');
            console.log(`[RENDER] Container found:`, container);

            if (!container) {
                console.error(`[RENDER] ERROR: poolsContainer not found!`);
                return;
            }

            // Remove loading message if present
            const loading = container.querySelector('.loading');
            if (loading) {
                console.log(`[RENDER] Removing loading message`);
                loading.remove();
            }
            // Insert new pool at the top
            console.log(`[RENDER] Rendering pool HTML for: ${pool.name}`);
            const newPoolHTML = renderPool(pool);

            // Find first element child (skip text nodes)
            const firstElementChild = container.firstElementChild;
            if (firstElementChild) {
                console.log(`[RENDER] Inserting pool before existing first child`);
                firstElementChild.insertAdjacentHTML('beforebegin', newPoolHTML);
            } else {
                console.log(`[RENDER] Container is empty, setting innerHTML directly`);
                container.innerHTML = newPoolHTML;
            }

            // Images are embedded as <img> tags directly in the HTML - no post-processing needed

            // Auto-fetch Meteora price data if valid token mint
            if (pool.base_mint && pool.base_mint.length === 44) {
                console.log(`[RENDER] Auto-fetching Meteora price for: ${pool.name} (${pool.base_mint})`);
                // Small delay to ensure DOM is updated
                setTimeout(() => {
                    fetchMeteoraPriceData(pool.base_mint);
                }, 100);
            }
        }

        function pollForNewPools() {
            // Poll for new pools every 1 second for near real-time updates
            // Try broadcast queue first, fallback to database
            fetch('/api/pools/new')
                .then(response => {
                    console.log(`[POLL] Fetch response status: ${response.status}`);
                    return response.json();
                })
                .then(data => {
                    console.log(`[POLL] Response received:`, data);
                    let pools = data.new_pools || [];

                    // If no new pools from broadcast queue, try database
                    if (!pools || pools.length === 0) {
                        console.log(`[POLL] No new pools in queue, trying database fallback...`);
                        return fetch('/api/pools').then(r => r.json()).then(dbData => {
                            pools = (dbData.pools || []).slice(0, 10); // Get last 10
                            return { pools: pools, isFromDatabase: true };
                        });
                    }
                    return { pools: pools, isFromDatabase: false };
                })
                .then(result => {
                    const pools = result.pools || [];
                    const isFromDatabase = result.isFromDatabase;

                    if (pools && pools.length > 0) {
                        console.log(`[POLL] ✓ Received ${pools.length} pool(s)${isFromDatabase ? ' from database' : ''}`);
                        console.log(`[POLL] Pool details:`, pools);

                        // Check if paused before adding new pools
                        if (isPaused) {
                            console.log(`[POLL] ⏸ Paused - ignoring ${pools.length} pool(s)`);
                        } else {
                            // Add each new pool to the UI
                            pools.forEach(pool => {
                                console.log(`[POLL] Adding pool: ${pool.name} (${pool.symbol}) with image: ${pool.image}`);
                                console.log(`[POLL] Pool first_seen: ${pool.first_seen}`);
                                addNewPoolToUI(pool);
                            });
                        }
                    } else {
                        console.log(`[POLL] No pools in response`);
                    }
                })
                .catch(error => {
                    console.error('[POLL] Error fetching pools:', error);
                    console.error('[POLL] Error details:', error.message);
                });
        }

        function updatePoolPrices() {
            // Poll for price updates for existing pools
            const poolElements = document.querySelectorAll('.pool-item');
            poolElements.forEach(poolEl => {
                const baseMint = poolEl.id.replace('pool-', '');
                if (baseMint && baseMint.length === 44) {
                    // Re-fetch price data for each pool
                    fetchMeteoraPriceData(baseMint);
                }
            });
        }

        // Poll for new pools every 1 second (near real-time updates)
        console.log('[INIT] Setting up polling interval (every 1 second)');
        setInterval(pollForNewPools, 1000);

        // Update pool prices every 2 seconds (faster updates)
        console.log('[INIT] Setting up price update interval (every 2 seconds)');
        setInterval(updatePoolPrices, 2000);

        // Update pool times every 1 second to show "X seconds ago" dynamically
        // Offset by 500ms to avoid race conditions with polling
        console.log('[INIT] Setting up time update interval (every 1 second, offset by 500ms)');
        setTimeout(() => {
            setInterval(updateAllPoolTimes, 1000);
        }, 500);

        // First poll should happen immediately to load initial pools
        console.log('[INIT] Running first immediate poll');
        pollForNewPools();

        // First time update should happen after a short delay
        setTimeout(updateAllPoolTimes, 100);

        console.log('[INIT] Application initialization complete');
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return HTML_TEMPLATE

@app.route('/api/pools')
def get_pools():
    """Get recent pools from database with prices

    Fallback endpoint when broadcast queue is empty (e.g., during WebSocket connection issues)
    Returns recent pools from database sorted by detection time
    """
    recent_pools = monitor.db.get_recent_pools(limit=50)
    return jsonify({'pools': recent_pools})

@app.route('/api/pools/new')
def get_new_pools():
    """Get new pools from broadcast queue with price updates (faster than 30s refresh)

    Returns pools that were added to the queue but haven't been polled yet.
    Includes price change percentage based on creation price vs current price.
    Clients should poll this every 1-2 seconds for near real-time updates.
    """
    new_pools = []

    # Drain the queue and collect all new pools
    queue_size_before = pool_broadcast_queue.qsize()
    while not pool_broadcast_queue.empty():
        try:
            pool = pool_broadcast_queue.get_nowait()

            # Calculate price change if we have both creation and current prices
            price_change_percent = 0
            if pool.get('creation_price') and pool.get('current_price') and pool.get('creation_price') != 0:
                price_change_percent = ((pool.get('current_price') - pool.get('creation_price')) / pool.get('creation_price')) * 100

            pool['price_change_percent'] = price_change_percent

            new_pools.append(pool)
            print(f"[API] Delivered pool to client: {pool.get('name')} ({pool.get('symbol')})")
            if pool.get('image'):
                print(f"[API] Image URL: {pool.get('image')}")
            if pool.get('current_price'):
                print(f"[API] Price change: {price_change_percent:.2f}%")
            sys.stdout.flush()
        except queue.Empty:
            break

    if new_pools:
        print(f"[API] Queue size before: {queue_size_before}, Returning {len(new_pools)} new pool(s)")
        sys.stdout.flush()
    else:
        if queue_size_before > 0:
            print(f"[API] WARNING: Queue had {queue_size_before} items but couldn't drain them!")
        # Uncomment for verbose logging: else: print(f"[API] Poll received, queue empty")

    return jsonify({'new_pools': new_pools})

@app.route('/api/pools/prices')
def get_updated_prices():
    """Get price updates for existing pools

    Returns pools that have had price updates since last poll.
    Used to update existing pool items in the UI without re-rendering.
    """
    # Get all pools with current prices
    all_pools = monitor.db.get_recent_pools(limit=100)

    # Filter pools that have price data
    pools_with_prices = []
    for pool in all_pools:
        if pool.get('creation_price') and pool.get('current_price') and pool.get('creation_price') != 0:
            price_change_percent = ((pool.get('current_price') - pool.get('creation_price')) / pool.get('creation_price')) * 100
            pools_with_prices.append({
                'amm_id': pool['amm_id'],
                'current_price': pool['current_price'],
                'creation_price': pool['creation_price'],
                'price_change_percent': price_change_percent,
                'dexscreener_price_usd': pool.get('dexscreener_price_usd'),
                'dexscreener_price_native': pool.get('dexscreener_price_native'),
                'sol_usd_price': pool.get('sol_usd_price')
            })

    return jsonify({'pools': pools_with_prices})

@app.route('/api/meteora/price/<token_mint>')
def get_meteora_price(token_mint):
    """Get Meteora pool price from database or fallback to live fetch

    First checks database for cached price data.
    Falls back to live fetch if not found in database.
    """
    try:
        print(f"[API PRICE] Fetching price for token_mint: {token_mint[:16]}...", flush=True)
        sys.stdout.flush()
        # Try to get from database first (most reliable)
        db = RaydiumDatabase()
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT current_price, total_supply, market_cap, dex, dexscreener_price_usd, dexscreener_price_native, sol_usd_price
            FROM pools
            WHERE base_mint = ?
            LIMIT 1
        ''', (token_mint,))

        result = cursor.fetchone()
        conn.close()

        if result:
            current_price, total_supply, market_cap, dex, dex_price_usd, dex_price_native, sol_usd_price = result
            print(f"[API PRICE] ✓ Found in database - on_chain: {current_price}, dex_usd: {dex_price_usd}, dex_native: {dex_price_native}, sol_rate: {sol_usd_price}", flush=True)
            sys.stdout.flush()

            # Build response from database
            response = {
                'on_chain_price': current_price,
                'source': 'database',
                'dex': dex,
                'total_supply': total_supply,
                'market_cap': market_cap,
            }

            # Add DexScreener data if available
            if dex_price_usd or dex_price_native:
                response['dexscreener_data'] = {
                    'priceUsd': dex_price_usd,
                    'priceNative': dex_price_native,
                }
                print(f"[API PRICE] ✓ Added DexScreener data to response")

                # Add SOL/USD rate if available
                if sol_usd_price:
                    response['sol_usd_price'] = sol_usd_price
                    # Convert on-chain price to USD
                    response['on_chain_price_usd'] = current_price * sol_usd_price if current_price else None
                    print(f"[API PRICE] ✓ Added SOL/USD rate and converted on-chain price to USD: {response['on_chain_price_usd']}")

                    # Calculate comparison metrics if both prices available
                    if current_price and dex_price_native:
                        if dex_price_native > 0:
                            ratio = current_price / dex_price_native
                            diff_pct = abs(current_price - dex_price_native) / dex_price_native * 100
                            response['comparison'] = {
                                'ratio': ratio,
                                'difference_pct': diff_pct,
                                'status': 'large_discrepancy' if diff_pct > 10 else 'matched'
                            }
            else:
                print(f"[API PRICE] ⚠ No DexScreener data for {token_mint[:16]}...")

            return jsonify(response)

        # Fallback: Try live fetch if not in database
        print(f"[API PRICE] ⚠ Pool {token_mint[:16]}... not found in database, attempting live fetch...")
        price_fetcher = MeteoraPriceFetcher()
        price_data = price_fetcher.fetch_price(token_mint)

        if not price_data:
            print(f"[API PRICE] ✗ Live fetch failed for {token_mint[:16]}...")
            return jsonify({'error': 'Could not fetch price'}), 404

        on_chain = price_data.get('on_chain_price')
        dex_data = price_data.get('dexscreener_data')

        print(f"[API PRICE] ✓ Live fetch succeeded - on_chain: {on_chain}, dex_usd: {dex_data.get('priceUsd') if dex_data else None}")

        response = {
            'on_chain_price': on_chain,
            'dexscreener_data': dex_data,
            'source': 'live_fetch'
        }

        # Calculate comparison metrics if both prices available
        if on_chain and dex_data:
            dex_native = dex_data.get('priceNative', 0)
            if dex_native > 0:
                ratio = on_chain / dex_native
                diff_pct = abs(on_chain - dex_native) / dex_native * 100
                response['comparison'] = {
                    'ratio': ratio,
                    'difference_pct': diff_pct,
                    'status': 'large_discrepancy' if diff_pct > 10 else 'matched'
                }

        return jsonify(response)
    except Exception as e:
        print(f"[API PRICE] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/pause', methods=['POST'])
def toggle_pause():
    """Toggle pause state for new pool listening
    
    When paused, the WebSocket listener still runs but ignores new pools.
    Price updates for existing pools continue normally.
    """
    global pause_new_pools
    
    with pause_lock:
        pause_new_pools = not pause_new_pools
        new_state = pause_new_pools
    
    state_text = "⏸ PAUSED" if new_state else "▶ LISTENING"
    print(f"[API] Pause state changed: {state_text}")
    
    return jsonify({
        'paused': new_state,
        'message': f'New pool listening {state_text}'
    })

@app.route('/api/pause-status', methods=['GET'])
def get_pause_status():
    """Get current pause state"""
    global pause_new_pools
    
    with pause_lock:
        current_state = pause_new_pools
    
    return jsonify({
        'paused': current_state
    })

def main():
    """Main function to run the monitor with web UI"""
    print("=" * 60)
    print("Raydium Token Monitor - WebSocket")
    print("=" * 60)

    PORT = 5002

    # Start WebSocket monitoring
    monitor.start_background_monitor()

    # Start price updater
    monitor.start_price_updater()

    # Start web server
    print(f"\nWeb UI: http://localhost:{PORT}")
    print("Press Ctrl+C to stop\n")

    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == "__main__":
    main()
