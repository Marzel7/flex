import sqlite3
import json
import asyncio
import websockets
from datetime import datetime
from typing import Dict, List
from flask import Flask, jsonify, Response
from threading import Thread
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
                last_updated TIMESTAMP
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

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_amm_id ON pools(amm_id)
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
            SELECT amm_id, name, symbol, image, base_mint, liquidity, price, signature, dex, first_seen
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
                'first_seen': row[9]
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

    def parse_pool_from_logs(self, logs: List[str], signature: str) -> Dict:
        """Parse pool creation from transaction logs and fetch token addresses"""
        import time
        
        WSOL = "So11111111111111111111111111111111111111112"
        
        pool_data = {
            'ammId': signature[:16],
            'name': 'Unknown',
            'baseMint': '',
            'quoteMint': '',
            'liquidity': 0,
            'price': 0,
            'signature': signature,
            'symbol': '',
            'image': ''
        }

        # Wait for transaction to be confirmed on chain
        time.sleep(3)

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
                    
                    print(f"Found mints: {mint_sources}")
                    
                    # Identify base and quote mints
                    quote_mint = WSOL if WSOL in mint_sources else None
                    
                    # The base mint is the new token being created
                    # It should NOT be WSOL
                    # Prefer mints that appear in postTokenBalances (actual pool state)
                    base_mint = None
                    
                    for mint, sources in mint_sources.items():
                        if mint == WSOL:
                            continue
                        # Mints in postTokenBalances are more reliable (actual pool accounts)
                        if 'post_balance' in sources:
                            base_mint = mint
                            break
                    
                    # If no mint in postTokenBalances, pick first non-WSOL
                    if not base_mint:
                        for mint in mint_sources.keys():
                            if mint != WSOL:
                                base_mint = mint
                                break
                    
                    # Get AMM ID from account keys (index 4 for Raydium V4)
                    account_keys = tx.get('transaction', {}).get('message', {}).get('accountKeys', [])
                    pubkeys = []
                    for key in account_keys:
                        if isinstance(key, dict):
                            pubkeys.append(key.get('pubkey', ''))
                        else:
                            pubkeys.append(key)
                    
                    if len(pubkeys) > 4:
                        pool_data['ammId'] = pubkeys[4]
                    
                    # Set mints in pool data
                    if base_mint:
                        pool_data['baseMint'] = base_mint
                        print(f"Base mint: {base_mint}")
                    if quote_mint:
                        pool_data['quoteMint'] = quote_mint
                        print(f"Quote mint: {quote_mint} (WSOL)")
                    
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
                                    print(f"\n{'='*50}")
                                    print(f"New {dex_source} pool launch: {signature}")

                                    pool_data = self.parse_pool_from_logs(logs, signature)
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
                                                'first_seen': datetime.now().isoformat()
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


# Flask Web Application
app = Flask(__name__)
monitor = RaydiumMonitor()

# Global queue for real-time pool broadcasts
pool_broadcast_queue = queue.Queue()

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
            /* CSS background-image will be set inline via style attribute */
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

        .pool-change {
            font-size: 12px;
            font-weight: 600;
            color: #00d084;
        }

        .pool-change.negative {
            color: #ff6b6b;
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
        function formatNumber(num) {
            if (num >= 1000000) {
                return '$' + (num / 1000000).toFixed(2) + 'M';
            } else if (num >= 1000) {
                return '$' + (num / 1000).toFixed(2) + 'K';
            }
            return '$' + num.toFixed(2);
        }

        function formatPrice(price) {
            if (price < 0.01) {
                return '$' + price.toFixed(8);
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

        function getInitials(name) {
            if (!name || name === 'Unknown') return '?';
            return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
        }

        function renderPool(pool) {
            const initials = getInitials(pool.name);
            const shortAddress = pool.base_mint ? pool.base_mint.slice(0, 8) + '...' + pool.base_mint.slice(-6) : 'Unknown';
            const changePercent = Math.floor(Math.random() * 400) - 100; // Simulated change
            const changeClass = changePercent >= 0 ? '' : 'negative';
            const dexBadge = pool.dex ? `<span style="background: #1a2847; padding: 2px 8px; border-radius: 4px; font-size: 10px; margin-left: 8px; color: #ffd700;">${pool.dex}</span>` : '';

            // Build pool icon: use image if available, otherwise use initials with gradient
            let iconHTML = '';
            if (pool.image && pool.image.trim()) {
                // Image available - store URL in data attribute, apply in JavaScript
                console.log(`[RENDER] Pool: ${pool.name}, Image URL: ${pool.image}`);
                iconHTML = `<div class="pool-icon has-image" id="icon-${pool.base_mint}" data-image-url="${pool.image.replace(/"/g, '&quot;')}"></div>`;
            } else {
                // No image - use initials with gradient background
                console.log(`[RENDER] Pool: ${pool.name}, No image URL`);
                iconHTML = `<div class="pool-icon">${initials}</div>`;
            }

            return `
                <div class="pool-item">
                    <div class="pool-left">
                        ${iconHTML}
                        <div class="pool-info">
                            <div class="pool-name" style="display: flex; align-items: center;">${pool.name || 'Unknown'} ${pool.symbol ? '(' + pool.symbol + ')' : ''} ${dexBadge}</div>
                            <div class="pool-address">
                                <a href="https://solscan.io/token/${pool.base_mint}" target="_blank" style="color: #8892b0; text-decoration: none;">${shortAddress}</a>
                            </div>
                        </div>
                    </div>
                    <div class="pool-right">
                        <div class="pool-time">${formatActiveTime(pool.first_seen)}</div>
                        <div class="pool-change ${changeClass}">${changePercent >= 0 ? '+' : ''}${changePercent}%</div>
                    </div>
                </div>
            `;
        }

        function updateStats(data) {
            document.getElementById('totalPools').textContent = data.total_pools.toLocaleString();
            document.getElementById('newToday').textContent = data.pools.length;
            const totalLiq = data.pools.reduce((sum, p) => sum + p.liquidity, 0);
            document.getElementById('totalLiquidity').textContent = formatNumber(totalLiq);
        }

        function updatePools() {
            console.log('[REFRESH] 30-second backup refresh - checking for pool updates');
            fetch('/api/pools')
                .then(response => response.json())
                .then(data => {
                    console.log('[REFRESH] Got pools from /api/pools:', data.pools.length);
                    // Update stats only, don't re-render all pools
                    // This prevents the full DOM re-render that was causing images to disappear
                    updateStats(data);

                    // Only update container on initial load (if it has loading message)
                    const container = document.getElementById('poolsContainer');
                    const loading = container.querySelector('.loading');
                    if (loading) {
                        console.log('[INIT] Replacing loading message with pools');
                        if (data.pools.length === 0) {
                            container.innerHTML = '<div class="loading">No pools found yet. Monitoring...</div>';
                        } else {
                            container.innerHTML = data.pools.map(renderPool).join('');
                        }
                    }
                    // Otherwise, don't touch the DOM to prevent flickering and re-fetching images
                    console.log('[REFRESH] Refresh complete - DOM not modified (preserving images)');
                })
                .catch(error => {
                    console.error('Error fetching pools:', error);
                });
        }

        function addNewPoolToUI(pool) {
            console.log(`[RENDER] addNewPoolToUI called for: ${pool.name}`);
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

            // Load images from data attributes - do this once per pool, never again
            // This prevents images from being affected by subsequent polling or data updates
            const iconElement = document.getElementById(`icon-${pool.base_mint}`);
            if (iconElement && iconElement.dataset.imageUrl) {
                const imageUrl = iconElement.dataset.imageUrl;
                // Use requestAnimationFrame to ensure this happens after DOM is fully painted
                requestAnimationFrame(() => {
                    iconElement.style.backgroundImage = `url("${imageUrl}")`;
                    iconElement.style.backgroundSize = 'cover';
                    iconElement.style.backgroundPosition = 'center';
                    iconElement.style.backgroundRepeat = 'no-repeat';
                    console.log(`[RENDER] ✓ Applied background-image for ${pool.name}`);
                });
            }
        }

        function pollForNewPools() {
            // Poll for new pools every 1 second for near real-time updates
            fetch('/api/pools/new')
                .then(response => {
                    console.log(`[POLL] Fetch response status: ${response.status}`);
                    return response.json();
                })
                .then(data => {
                    console.log(`[POLL] Response received:`, data);
                    if (data.new_pools && data.new_pools.length > 0) {
                        console.log(`[POLL] ✓ Received ${data.new_pools.length} new pool(s)`);
                        console.log(`[POLL] Pool details:`, data.new_pools);
                        // Add each new pool to the UI
                        data.new_pools.forEach(pool => {
                            console.log(`[POLL] Adding pool: ${pool.name} (${pool.symbol}) with image: ${pool.image}`);
                            addNewPoolToUI(pool);
                        });
                    } else {
                        console.log(`[POLL] No new pools in response`);
                    }
                })
                .catch(error => {
                    console.error('[POLL] Error fetching new pools:', error);
                    console.error('[POLL] Error details:', error.message);
                });
        }

        // Initial load
        console.log('[INIT] Starting initial pool load');
        updatePools();

        // Poll for new pools every 1 second (near real-time updates)
        console.log('[INIT] Setting up polling interval (every 1 second)');
        setInterval(pollForNewPools, 1000);

        // First poll should happen immediately
        console.log('[INIT] Running first immediate poll');
        pollForNewPools();

        // Note: 30-second backup refresh DISABLED - it was interfering with image loading
        // We rely on 1-second polling via /api/pools/new for real-time updates
        // Stats will be slightly out of date but images will load properly
        // If needed in future, stats can be updated less frequently (every 5 minutes+)

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
    """API endpoint to get new pools"""
    try:
        pools = monitor.db.get_recent_pools(50)
        return jsonify({
            'pools': pools,
            'total_pools': monitor.db.get_pool_count()
        })
    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({'pools': [], 'total_pools': 0})

@app.route('/api/pools/new')
def get_new_pools():
    """Get new pools from broadcast queue (faster than 30s refresh)

    Returns pools that were added to the queue but haven't been polled yet.
    Clients should poll this every 1-2 seconds for near real-time updates.
    """
    new_pools = []

    # Drain the queue and collect all new pools
    queue_size_before = pool_broadcast_queue.qsize()
    while not pool_broadcast_queue.empty():
        try:
            pool = pool_broadcast_queue.get_nowait()
            new_pools.append(pool)
            print(f"[API] Delivered pool to client: {pool.get('name')} ({pool.get('symbol')})")
            if pool.get('image'):
                print(f"[API] Image URL: {pool.get('image')}")
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

def main():
    """Main function to run the monitor with web UI"""
    print("=" * 60)
    print("Raydium Token Monitor - WebSocket")
    print("=" * 60)

    PORT = 5002

    # Start WebSocket monitoring
    monitor.start_background_monitor()

    # Start web server
    print(f"\nWeb UI: http://localhost:{PORT}")
    print("Press Ctrl+C to stop\n")

    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == "__main__":
    main()
