#!/usr/bin/env python3
"""
Analyze creator's wallet transaction history and on-chain behavior.

Examines:
- Total transactions and activity volume
- Fund flows (incoming vs outgoing)
- Token holdings and distribution patterns
- Trading behavior across multiple tokens
- Wallet age and activity timeline
- Suspicious patterns (rapid fund movements, multiple wallets, etc.)
"""

import sqlite3
from pathlib import Path
import sys
import os

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def get_creator_info(creator_address):
    """Get creator info from database"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return None

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                COUNT(*) as token_count,
                SUM(CASE WHEN trade_status = 'sold' THEN 1 ELSE 0 END) as sold_count,
                SUM(CASE WHEN buy_price_usd IS NOT NULL THEN quantity_bought ELSE 0 END) as total_bought,
                SUM(CASE WHEN sell_price_usd IS NOT NULL THEN quantity_sold ELSE 0 END) as total_sold,
                SUM(profit_loss_usd) as total_profit_usd,
                AVG(profit_loss_percent) as avg_profit_pct,
                MIN(first_seen) as first_token_date,
                MAX(first_seen) as latest_token_date
            FROM pools
            WHERE pumpfun_creator = ?
        ''', (creator_address,))

        stats = cursor.fetchone()
        conn.close()

        return {
            'token_count': stats[0] or 0,
            'sold_count': stats[1] or 0,
            'total_bought': stats[2] or 0,
            'total_sold': stats[3] or 0,
            'total_profit_usd': stats[4] or 0,
            'avg_profit_pct': stats[5] or 0,
            'first_token_date': stats[6],
            'latest_token_date': stats[7]
        }

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def fetch_helius_transactions(wallet_address, fetch_all=False):
    """
    Fetch transaction history from Helius API (free tier available)

    Args:
        wallet_address: The wallet address to fetch transactions for
        fetch_all: If True, fetch all available transactions with pagination.
                   If False (default), fetch only the last 100 transactions.

    Returns:
        List of transactions or None if error
    """
    if not HAS_REQUESTS:
        print("⚠️  requests library not installed. Install with: pip install requests")
        return None

    # Get API key from environment variable
    api_key = os.getenv('HELIUS_API_KEY')

    if not api_key:
        # No API key available
        return None

    try:
        # Helius API endpoint for transaction history
        url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{wallet_address}/transactions"
        all_transactions = []
        pagination_token = None
        request_count = 0
        max_requests = 50  # Safety limit to prevent infinite loops

        while request_count < max_requests:
            params = {
                "api-key": api_key,
                "limit": 100  # Helius max is 100 per request
            }

            # Add pagination token if we have one (for fetching additional pages)
            if pagination_token:
                params["before"] = pagination_token

            response = requests.get(url, params=params, timeout=10)
            request_count += 1

            if response.status_code == 200:
                data = response.json()

                if isinstance(data, list):
                    all_transactions.extend(data)

                    # If we got fewer transactions than the limit, we've reached the end
                    if len(data) < 100:
                        break

                    # If not fetching all, return after first batch
                    if not fetch_all:
                        return all_transactions

                    # For pagination, get the signature of the last transaction
                    # to use as the "before" parameter for the next request
                    if data:
                        last_tx = data[-1]
                        pagination_token = last_tx.get('signature')
                        if not pagination_token:
                            break
                else:
                    return all_transactions if all_transactions else data

            elif response.status_code == 401:
                # Invalid API key
                return None
            elif response.status_code == 429:
                print(f"⚠️  Rate limited by Helius API. Try again in a moment.")
                return None
            else:
                return all_transactions if all_transactions else None

        return all_transactions if all_transactions else None

    except Exception as e:
        return None


def fetch_magic_eden_activity(creator_address):
    """Fetch NFT/token activity from Magic Eden or similar"""
    print("⚠️  Magic Eden integration coming soon...")
    return None


def is_valid_solana_address(addr):
    """Check if address is a valid Solana address (44 chars, Base58)"""
    if not isinstance(addr, str):
        return False
    # Solana addresses are 44 characters, Base58 encoded
    if len(addr) != 44:
        return False
    # Check if it's valid Base58 (no 0, O, I, or l characters)
    invalid_chars = set('0OIl')
    if any(c in addr for c in invalid_chars):
        return False
    return True


def analyze_sol_transfers(transactions, creator_address):
    """
    Analyze SOL transfers IN and OUT from the creator's wallet.

    Returns dict with SOL flow analysis including:
    - Total SOL in/out
    - Transfer destinations and sources
    - Timing patterns

    Uses nativeTransfers field from Helius API for accurate transfer data.
    Falls back to description parsing if nativeTransfers unavailable.
    """
    from datetime import datetime

    sol_in = []  # {timestamp, amount, source, tx_sig}
    sol_out = []  # {timestamp, amount, destination, tx_sig}

    for tx in transactions:
        if not isinstance(tx, dict):
            continue

        tx_type = tx.get('type', '').lower()
        timestamp = tx.get('timestamp')
        signature = tx.get('signature', 'unknown')[:16]

        # Only process TRANSFER type transactions
        if tx_type != 'transfer':
            continue

        # Use nativeTransfers if available (most accurate)
        native_transfers = tx.get('nativeTransfers', [])
        if native_transfers:
            for transfer in native_transfers:
                from_addr = transfer.get('fromUserAccount')
                to_addr = transfer.get('toUserAccount')
                amount = transfer.get('amount', 0)

                # Convert lamports to SOL
                amount_sol = amount / 1_000_000_000

                # Skip dust (less than 0.000001 SOL)
                if amount_sol < 0.000001:
                    continue

                # Check if addresses are valid
                if not is_valid_solana_address(from_addr) or not is_valid_solana_address(to_addr):
                    continue

                # Determine direction
                if from_addr.lower() == creator_address.lower():
                    # Creator is sending (outgoing)
                    sol_out.append({
                        'timestamp': timestamp,
                        'amount': amount_sol,
                        'destination': to_addr,
                        'sig': signature
                    })
                elif to_addr.lower() == creator_address.lower():
                    # Creator is receiving (incoming)
                    sol_in.append({
                        'timestamp': timestamp,
                        'amount': amount_sol,
                        'source': from_addr,
                        'sig': signature
                    })
        else:
            # Fallback: parse from description if nativeTransfers unavailable
            description = tx.get('description', '').lower()

            # Skip any token-related transfers
            if 'token' in description or 'spl' in description or 'mint' in description:
                continue

            # Look for valid Solana addresses in the description
            words = description.split()
            valid_addresses = [w for w in words if is_valid_solana_address(w)]

            if not valid_addresses:
                continue

            # Try to find amount in the transaction
            try:
                amount = None
                parts = description.split()
                for i, part in enumerate(parts):
                    if 'sol' in part.lower() and i > 0:
                        try:
                            amount = float(parts[i-1])
                            break
                        except ValueError:
                            continue

                if amount is None or amount <= 0:
                    continue

                # Determine direction based on addresses
                if valid_addresses:
                    first_addr = valid_addresses[0]
                    dest_addr = valid_addresses[-1] if len(valid_addresses) > 1 else first_addr

                    if first_addr.lower() == creator_address.lower():
                        # Outgoing transfer
                        sol_out.append({
                            'timestamp': timestamp,
                            'amount': amount,
                            'destination': dest_addr,
                            'sig': signature
                        })
                    else:
                        # Incoming transfer
                        sol_in.append({
                            'timestamp': timestamp,
                            'amount': amount,
                            'source': first_addr,
                            'sig': signature
                        })
            except Exception:
                continue

    return {
        'sol_in': sol_in,
        'sol_out': sol_out,
        'total_in': sum(t['amount'] for t in sol_in),
        'total_out': sum(t['amount'] for t in sol_out),
    }


def get_creator_tokens(creator_address):
    """Get all tokens created by this creator from database"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                base_mint,
                symbol,
                pumpfun_symbol,
                first_seen,
                peak_percent_change,
                peak_time,
                buy_price_usd,
                sell_price_usd,
                trade_status
            FROM pools
            WHERE pumpfun_creator = ?
            ORDER BY first_seen DESC
        ''', (creator_address,))

        tokens = cursor.fetchall()
        conn.close()
        return tokens
    except:
        return []


def store_creator_wallet_data(creator_address, wallet_stats, sol_transfers):
    """
    Store creator wallet data and SOL transfer accounts in database.

    Args:
        creator_address: Creator wallet address
        wallet_stats: Dict with account_age_days, first_tx_timestamp, total_txs, etc.
        sol_transfers: Dict with 'sol_in' and 'sol_out' lists containing transfer details
    """
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        return False

    try:
        from datetime import datetime
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Insert or update creator_wallets record
        cursor.execute('''
            INSERT OR REPLACE INTO creator_wallets (
                creator_address,
                account_age_days,
                first_transaction_timestamp,
                total_transactions,
                swap_count,
                transfer_count,
                total_sol_in,
                total_sol_out,
                net_sol_position,
                unique_wallet_interactions,
                last_analyzed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            creator_address,
            wallet_stats.get('account_age_days', 0),
            wallet_stats.get('first_tx_timestamp'),
            wallet_stats.get('total_transactions', 0),
            wallet_stats.get('swap_count', 0),
            wallet_stats.get('transfer_count', 0),
            wallet_stats.get('total_sol_in', 0),
            wallet_stats.get('total_sol_out', 0),
            wallet_stats.get('net_sol_position', 0),
            wallet_stats.get('unique_wallet_interactions', 0),
            datetime.now()
        ))

        # Store incoming SOL transfers (from other addresses to creator)
        # First, group by source to identify treasury accounts (>5 transfers)
        incoming_by_source = {}
        for transfer in sol_transfers.get('sol_in', []):
            source = transfer.get('source', 'unknown')
            amount = transfer.get('amount', 0)
            timestamp = transfer.get('timestamp')

            if source not in incoming_by_source:
                incoming_by_source[source] = {
                    'total': 0,
                    'count': 0,
                    'first_ts': timestamp,
                    'last_ts': timestamp
                }

            incoming_by_source[source]['total'] += amount
            incoming_by_source[source]['count'] += 1
            incoming_by_source[source]['last_ts'] = timestamp

        # Insert incoming transfers, marking treasury accounts
        for source, data in incoming_by_source.items():
            is_treasury = 1 if data['count'] > 5 else 0  # Treasury if >5 transfers

            cursor.execute('''
                INSERT OR REPLACE INTO creator_sol_transfers (
                    creator_address,
                    transfer_type,
                    counterparty_address,
                    total_amount,
                    transfer_count,
                    first_transfer_timestamp,
                    last_transfer_timestamp,
                    is_treasury
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                creator_address,
                'incoming',
                source,
                data['total'],
                data['count'],
                data['first_ts'],
                data['last_ts'],
                is_treasury
            ))

        # Store outgoing SOL transfers (from creator to other addresses)
        # First, group by destination to identify treasury addresses
        outgoing_by_dest = {}
        for transfer in sol_transfers.get('sol_out', []):
            dest = transfer.get('destination', 'unknown')
            amount = transfer.get('amount', 0)
            timestamp = transfer.get('timestamp')

            if dest not in outgoing_by_dest:
                outgoing_by_dest[dest] = {
                    'total': 0,
                    'count': 0,
                    'first_ts': timestamp,
                    'last_ts': timestamp
                }

            outgoing_by_dest[dest]['total'] += amount
            outgoing_by_dest[dest]['count'] += 1
            outgoing_by_dest[dest]['last_ts'] = timestamp

        # Insert outgoing transfers, marking treasury addresses
        for dest, data in outgoing_by_dest.items():
            is_treasury = 1 if data['count'] > 5 else 0  # Treasury if >5 transfers

            cursor.execute('''
                INSERT OR REPLACE INTO creator_sol_transfers (
                    creator_address,
                    transfer_type,
                    counterparty_address,
                    total_amount,
                    transfer_count,
                    first_transfer_timestamp,
                    last_transfer_timestamp,
                    is_treasury
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                creator_address,
                'outgoing',
                dest,
                data['total'],
                data['count'],
                data['first_ts'],
                data['last_ts'],
                is_treasury
            ))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"⚠️  Error storing creator wallet data: {e}")
        return False


def analyze_creator_wallet(creator_address):
    """Analyze creator's wallet and transaction patterns"""
    creator_short = f"{creator_address[:8]}...{creator_address[-4:]}"

    print(f"\n{'='*160}")
    print(f"CREATOR WALLET ANALYSIS: {creator_short}")
    print(f"Full Address: {creator_address}")
    print(f"{'='*160}\n")

    # Get database info
    db_info = get_creator_info(creator_address)

    if not db_info:
        print("❌ Creator not found in database")
        return

    print("DATABASE STATISTICS (from tracked tokens)")
    print("-" * 160)
    print(f"  Tokens launched: {db_info['token_count']}")
    if db_info['token_count'] > 0:
        print(f"  Tokens sold: {db_info['sold_count']} ({db_info['sold_count']/db_info['token_count']*100:.1f}% exit rate)")
    else:
        print(f"  Tokens sold: 0 (no tokens in database)")
    print(f"  Total quantity bought: {db_info['total_bought']:.2e}")
    print(f"  Total quantity sold: {db_info['total_sold']:.2e}")
    print(f"  Total realized profit: ${db_info['total_profit_usd']:.2f}")
    print(f"  Average profit %: {db_info['avg_profit_pct']:+.2f}%")
    print()

    # Get on-chain transaction data
    print("ON-CHAIN TRANSACTION DATA")
    print("-" * 160)

    # Provide direct Solscan link for manual inspection
    print("🔗 DIRECT WALLET INSPECTION (Recommended):")
    print(f"   https://solscan.io/address/{creator_address}")
    print()

    # Try API if key is available
    tx_data = fetch_helius_transactions(creator_address, fetch_all=False)

    if tx_data and isinstance(tx_data, list):
        print(f"✓ Successfully fetched transaction history from Helius API")

        transactions = tx_data

        if isinstance(transactions, list):
            tx_count = len(transactions)
            print(f"  Total transactions fetched: {tx_count}")
            if tx_count == 100:
                print(f"  ℹ️  Limited to last 100 transactions. Run with --full flag to fetch complete history.")
            else:
                print(f"  ✓ Complete transaction history loaded ({tx_count} total)")

            # Calculate account age from oldest transaction
            if transactions:
                oldest_tx = transactions[-1]  # Last in list is oldest
                oldest_timestamp = oldest_tx.get('timestamp')
                if oldest_timestamp:
                    from datetime import datetime, timedelta
                    oldest_date = datetime.fromtimestamp(oldest_timestamp)
                    now = datetime.now()
                    account_age = now - oldest_date
                    days = account_age.days
                    months = days // 30
                    years = days // 365

                    age_str = ""
                    if years > 0:
                        age_str = f"{years}y {months % 12}m"
                    elif months > 0:
                        age_str = f"{months}m {days % 30}d"
                    else:
                        age_str = f"{days}d"

                    print(f"  Account age: {age_str} (First transaction: {oldest_date.strftime('%Y-%m-%d %H:%M:%S')})")

                    # Risk assessment based on age
                    if days < 30:
                        print(f"  ⚠️  VERY NEW ACCOUNT - Created less than 1 month ago")
                    elif days < 90:
                        print(f"  ⚠️  NEW ACCOUNT - Created less than 3 months ago")
                    elif days < 180:
                        print(f"  • RELATIVELY NEW - Created less than 6 months ago")
                    else:
                        print(f"  ✓ ESTABLISHED - Account older than 6 months")

            # Analyze transaction types
            print()
            print("TRANSACTION ANALYSIS")
            print("-" * 160)

            # Categorize transactions
            swap_count = 0
            transfer_count = 0
            token_mint = 0
            successful_txs = 0
            swap_types = []
            wallet_interactions = set()

            for tx in transactions:
                if isinstance(tx, dict):
                    tx_type = tx.get('type', '').lower()
                    description = tx.get('description', '').lower()

                    # Detect swaps
                    if 'swap' in tx_type or 'swap' in description or 'jupiteragg' in description:
                        swap_count += 1
                        if description not in swap_types:
                            swap_types.append(description[:60])  # Track swap patterns

                    # Detect transfers
                    if 'transfer' in tx_type or 'transfer' in description or 'token 2022' in description:
                        transfer_count += 1

                    # Detect token creation
                    if 'initializemint' in description or 'createtoken' in description or 'init' in tx_type:
                        token_mint += 1

                    # Count interactions with other wallets
                    if 'source' in tx:
                        wallet_interactions.add(tx.get('source', 'unknown'))

            print(f"  Total transactions: {len(transactions)}")
            print(f"  Swaps detected: {swap_count} ({swap_count/len(transactions)*100:.1f}%)")
            print(f"  Transfers detected: {transfer_count}")
            print(f"  Token creations detected: {token_mint}")
            print(f"  Unique wallet interactions: {len(wallet_interactions)}")

            # Highlight high swap activity
            if swap_count > 50:
                print(f"  ⚠️  HIGH SWAP ACTIVITY: {swap_count} swaps in last 100 tx - active trader")
            elif swap_count > 25:
                print(f"  • MODERATE SWAP ACTIVITY: {swap_count} swaps")
            print()

            # Show recent transactions
            print("RECENT TRANSACTIONS (Last 10)")
            print("-" * 160)

            table_data = []
            for tx in transactions[:10]:
                if isinstance(tx, dict):
                    sig = tx.get('signature', 'unknown')[:16]
                    ts = tx.get('timestamp', None)
                    tx_type = tx.get('type', 'unknown')
                    description = tx.get('description', '')[:50]

                    # Format timestamp
                    ts_str = 'unknown'
                    if ts is not None:
                        try:
                            from datetime import datetime
                            # Helius returns timestamps as seconds since epoch
                            if isinstance(ts, (int, float)):
                                ts_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            ts_str = str(ts)

                    # Parse transaction type for better display
                    if 'swap' in str(tx).lower():
                        icon = "🔄"
                    elif 'transfer' in str(tx).lower():
                        icon = "↔️ "
                    elif 'withdraw' in str(tx).lower():
                        icon = "⬇️"
                    elif 'deposit' in str(tx).lower():
                        icon = "⬆️"
                    else:
                        icon = "•"

                    table_data.append([f"{icon} {sig}", tx_type, description, ts_str])

            if HAS_TABULATE:
                headers = ['Signature', 'Type', 'Description', 'Timestamp']
                print(tabulate(table_data, headers=headers, tablefmt='grid'))
            else:
                print(f"{'Signature':<20} | {'Type':<12} | {'Description':<50} | {'Timestamp':<19}")
                print("-" * 105)
                for row in table_data:
                    print(f"{row[0]:<20} | {row[1]:<12} | {row[2]:<50} | {row[3]:<19}")

            # Transaction count summary by type
            print()
            print("TRANSACTION COUNT BY TYPE")
            print("-" * 160)

            tx_type_count = {}
            for tx in transactions:
                if isinstance(tx, dict):
                    tx_type = tx.get('type', 'unknown').lower()
                    tx_type_count[tx_type] = tx_type_count.get(tx_type, 0) + 1

            # Sort by count descending
            sorted_tx_types = sorted(tx_type_count.items(), key=lambda x: x[1], reverse=True)

            if HAS_TABULATE:
                table_data = [[tx_type.upper(), count] for tx_type, count in sorted_tx_types]
                headers = ['Transaction Type', 'Count']
                print(tabulate(table_data, headers=headers, tablefmt='grid'))
            else:
                print(f"{'Transaction Type':<25} | {'Count':<10}")
                print("-" * 40)
                for tx_type, count in sorted_tx_types:
                    print(f"{tx_type.upper():<25} | {count:<10}")

            # Analyze token launches vs wallet activity
            print()
            print("TOKEN LAUNCHES & WALLET CORRELATION")
            print("-" * 160)

            creator_tokens = get_creator_tokens(creator_address)
            if creator_tokens:
                print(f"  Creator has {len(creator_tokens)} token(s) in the database\n")

                from datetime import datetime
                for idx, token in enumerate(creator_tokens, 1):
                    (mint, symbol, pf_symbol, first_seen, peak_pct, peak_time,
                     buy_price, sell_price, status) = token

                    token_name = symbol or pf_symbol or mint[:8]

                    # Parse token creation time
                    token_date = None
                    if first_seen:
                        try:
                            if isinstance(first_seen, str):
                                # Try parsing as datetime string (handles both "2026-01-05 00:48:21.581129" and ISO formats)
                                if 'T' in first_seen or ' ' in first_seen:
                                    # Remove microseconds for parsing
                                    date_str = first_seen.split('.')[0] if '.' in first_seen else first_seen
                                    date_str = date_str.replace('Z', '').replace('+00:00', '')
                                    try:
                                        token_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                                    except:
                                        token_date = datetime.fromisoformat(date_str)
                                else:
                                    # Try parsing as numeric string
                                    ts = float(first_seen)
                                    if ts > 1e10:  # Milliseconds
                                        token_date = datetime.fromtimestamp(ts / 1000)
                                    else:  # Seconds
                                        token_date = datetime.fromtimestamp(ts)
                            elif isinstance(first_seen, (int, float)):
                                if first_seen > 1e10:  # Milliseconds
                                    token_date = datetime.fromtimestamp(first_seen / 1000)
                                else:  # Seconds
                                    token_date = datetime.fromtimestamp(first_seen)
                        except Exception as e:
                            pass

                    if token_date:
                        print(f"  {idx}. {token_name} ({mint[:8]}...)")
                        print(f"     Token created: {token_date.strftime('%Y-%m-%d %H:%M:%S')}")
                        if peak_pct and peak_pct > 0:
                            print(f"     Peak: +{peak_pct:.2f}%")
                        else:
                            print(f"     Peak: Not reached")
                        print(f"     Status: {status}")

                        # Find wallet transactions near token creation
                        token_time = token_date.timestamp()
                        nearby_txs = []
                        for tx in transactions:
                            if isinstance(tx, dict):
                                tx_ts = tx.get('timestamp', 0)
                                time_diff = abs(tx_ts - token_time)
                                # Look for transactions within 24 hours
                                if time_diff < 86400:
                                    nearby_txs.append({
                                        'sig': tx.get('signature', 'unknown')[:16],
                                        'type': tx.get('type', 'unknown'),
                                        'desc': tx.get('description', 'unknown')[:80],
                                        'time_diff_seconds': time_diff,
                                        'ts': tx_ts
                                    })

                        if nearby_txs:
                            nearby_txs.sort(key=lambda x: x['time_diff_seconds'])
                            print(f"     ⏱️  Wallet activity within ±24 hours of launch:")
                            for nearby in nearby_txs[:3]:  # Show top 3 closest transactions
                                hours_diff = nearby['time_diff_seconds'] / 3600
                                time_label = f"{hours_diff:.1f}h before" if nearby['ts'] < token_time else f"{hours_diff:.1f}h after"
                                print(f"        • {nearby['type']}: {nearby['desc'][:60]} ({time_label})")
                        else:
                            print(f"     ℹ️  No wallet activity within ±24 hours of launch")
                        print()
                    else:
                        # Could not parse token date, still show basic info
                        print(f"  {idx}. {token_name} ({mint[:8]}...)")
                        print(f"     Created: {first_seen}")
                        if peak_pct and peak_pct > 0:
                            print(f"     Peak: +{peak_pct:.2f}%")
                        print(f"     Status: {status}")
                        print()
            else:
                print(f"  No tokens found in database for this creator")

            # SOL transfer analysis
            print()
            print("SOL TRANSFER ANALYSIS")
            print("-" * 160)

            sol_analysis = analyze_sol_transfers(transactions, creator_address)
            print(f"  Total SOL received: {sol_analysis['total_in']:.4f} SOL")
            print(f"  Total SOL sent out: {sol_analysis['total_out']:.4f} SOL")
            print(f"  Net SOL position: {sol_analysis['total_in'] - sol_analysis['total_out']:+.4f} SOL")
            print()

            if sol_analysis['sol_in']:
                print(f"  Incoming SOL transfers: {len(sol_analysis['sol_in'])}")
                print()

                # Group by source to aggregate amounts
                sources = {}
                for transfer in sol_analysis['sol_in']:
                    src = transfer['source']
                    if src not in sources:
                        sources[src] = {'count': 0, 'total': 0}
                    sources[src]['count'] += 1
                    sources[src]['total'] += transfer['amount']

                # Sort by total amount descending
                sorted_sources = sorted(sources.items(), key=lambda x: x[1]['total'], reverse=True)

                # Display as table if tabulate available, otherwise plain text
                if HAS_TABULATE:
                    table_data = []
                    for src, data in sorted_sources:
                        treasury = "🏦 Treasury" if data['count'] > 5 else ""
                        table_data.append([
                            src,
                            f"{data['total']:.4f}",
                            data['count'],
                            treasury
                        ])
                    headers = ['Source Address', 'SOL Amount', 'Transfers', 'Type']
                    print(tabulate(table_data, headers=headers, tablefmt='grid'))
                else:
                    # Plain text format
                    print(f"{'Source Address':<45} | {'SOL Amount':<12} | {'Transfers':<10} | Type")
                    print("-" * 80)
                    for src, data in sorted_sources:
                        treasury = "🏦 Treasury" if data['count'] > 5 else ""
                        print(f"{src:<45} | {data['total']:<12.4f} | {data['count']:<10} | {treasury}")
            else:
                print(f"  ℹ️  No incoming SOL transfers detected")

            print()

            if sol_analysis['sol_out']:
                print(f"  Outgoing SOL transfers: {len(sol_analysis['sol_out'])}")
                print()

                # Group by destination to see if there's a pattern
                destinations = {}
                for transfer in sol_analysis['sol_out']:
                    dest = transfer['destination']
                    if dest not in destinations:
                        destinations[dest] = {'count': 0, 'total': 0}
                    destinations[dest]['count'] += 1
                    destinations[dest]['total'] += transfer['amount']

                # Sort by total amount descending
                sorted_dests = sorted(destinations.items(), key=lambda x: x[1]['total'], reverse=True)

                # Display as table if tabulate available, otherwise plain text
                if HAS_TABULATE:
                    table_data = []
                    for dest, data in sorted_dests:
                        treasury = "🏦 Treasury" if data['count'] > 5 else ""
                        table_data.append([
                            dest,
                            f"{data['total']:.4f}",
                            data['count'],
                            treasury
                        ])
                    headers = ['Destination Address', 'SOL Amount', 'Transfers', 'Type']
                    print(tabulate(table_data, headers=headers, tablefmt='grid'))
                else:
                    # Plain text format
                    print(f"{'Destination Address':<45} | {'SOL Amount':<12} | {'Transfers':<10} | Type")
                    print("-" * 80)
                    for dest, data in sorted_dests:
                        treasury = "🏦 Treasury" if data['count'] > 5 else ""
                        print(f"{dest:<45} | {data['total']:<12.4f} | {data['count']:<10} | {treasury}")
            else:
                print(f"  ℹ️  No outgoing SOL transfers detected")

            # Store all collected data to database
            print()
            print("STORING WALLET DATA")
            print("-" * 160)

            # Prepare wallet statistics
            from datetime import datetime
            wallet_stats = {
                'account_age_days': days if transactions else 0,
                'first_tx_timestamp': oldest_date.strftime('%Y-%m-%d %H:%M:%S') if transactions else None,
                'total_transactions': len(transactions),
                'swap_count': swap_count,
                'transfer_count': transfer_count,
                'total_sol_in': sol_analysis['total_in'],
                'total_sol_out': sol_analysis['total_out'],
                'net_sol_position': sol_analysis['total_in'] - sol_analysis['total_out'],
                'unique_wallet_interactions': len(wallet_interactions)
            }

            # Store to database
            if store_creator_wallet_data(creator_address, wallet_stats, sol_analysis):
                print("  ✓ Wallet analysis stored to creator_wallets table")
                print(f"  ✓ Stored {len(sol_analysis['sol_in'])} incoming SOL transfer accounts")
                print(f"  ✓ Stored {len(sol_analysis['sol_out'])} outgoing SOL transfer accounts")
            else:
                print("  ⚠️  Could not store wallet data to database")

        else:
            print(f"⚠️  Unexpected response format from API")
    else:
        print("⚠️  API data not available (requires Helius API key)")
        print()
        print("MANUAL WALLET INSPECTION CHECKLIST:")
        print("-" * 160)
        print()
        print("When you open the Solscan link above, look for these patterns:\n")

        print("📊 GENERAL METRICS:")
        print("  □ Wallet age - New wallet (suspicious) vs established (trustworthy)")
        print("  □ Total SOL balance - High balance vs low")
        print("  □ Total transactions - Active (100+) vs inactive (<10)")
        print("  □ Token holdings - How many different SPL tokens do they hold?")
        print()

        print("💰 FUND FLOWS:")
        print("  □ SOL inflows - Where does their funding come from?")
        print("    • From exchange wallets? (suspicious)")
        print("    • From other private wallets? (potential multi-wallet control)")
        print("    • Consistent funding source? (organized operation)")
        print("  □ SOL outflows - Where do profits go?")
        print("    • To same addresses repeatedly? (treasury/main wallet)")
        print("    • Dispersed to many wallets? (fund splitting/mixing)")
        print("  □ Frequency of movements - Daily, weekly, sporadic?")
        print()

        print("🔄 TRANSACTION PATTERNS:")
        print("  □ Swap activity - Are they swapping tokens before launches?")
        print("  □ Token creation - Do they create tokens themselves?")
        print("  □ Timing patterns - Buy before/after token launches?")
        print("    • Before launch = potential insider knowledge")
        print("    • After launch = following public signals")
        print("  □ Success rate - What % of swaps are successful?")
        print()

        print("⚠️  RED FLAGS TO WATCH FOR:")
        print("  ⚠️  Recent wallet (created <1 month ago)")
        print("  ⚠️  Rapid fund movements (many transactions per hour)")
        print("  ⚠️  Multiple wallet connections (they control other wallets)")
        print("  ⚠️  Large SOL deposits before token launches")
        print("  ⚠️  Immediate profit extraction (buys, then sells within minutes)")
        print("  ⚠️  Consistent pump & dump timing")
        print("  ⚠️  No holding period (never holds tokens long-term)")
        print("  ⚠️  Wash trading signatures (rapid buy-sell with same counterparty)")
        print()

        print("✓ POSITIVE INDICATORS:")
        print("  ✓ Wallet age >6 months")
        print("  ✓ Diverse token holdings (not just pumps)")
        print("  ✓ Holding periods (keeps tokens 1-7 days)")
        print("  ✓ Mixed results (some wins, some losses - shows random selection)")
        print("  ✓ Consistent SOL reserves (not depleting)")
        print("  ✓ Stable transaction patterns (predictable rhythm)")
        print()

        print("HOW TO USE SOLSCAN TOOLS:")
        print("-" * 160)
        print("  1. Click 'Token' tab to see all SPL tokens in wallet")
        print("  2. Click 'Transaction' tab to see full history")
        print("  3. Look for 'Swap' transactions - these show their trading activity")
        print("  4. Check transaction details - right-click → 'View on Explorer'")
        print("  5. Trace fund sources - click on incoming transaction sender")
        print()

        print("AUTOMATED API SETUP (using Helius - FREE):")
        print("-" * 160)
        print("  Your project already has a Helius API key configured!")
        print()
        print("  Option 1: If using environment file:")
        print("     Make sure HELIUS_API_KEY is set in your .env file")
        print()
        print("  Option 2: Set directly in terminal:")
        print("     export HELIUS_API_KEY=your_helius_api_key")
        print("     python analyze_creator_wallet.py <creator_address>")
        print()
        print("  The tool will automatically fetch transaction data from Helius.")
        print()
        print("  To get a free Helius API key:")
        print("     Visit: https://www.helius.dev/")
        print("     Free tier includes 1M monthly credits (plenty for this analysis)")
        print()
        print("  ⚠️  SECURITY: Never paste API keys in code or commit them!")
        print()

    # Risk assessment based on available data
    print("RISK ASSESSMENT & PATTERNS")
    print("-" * 160)

    risk_factors = []

    # Check exit rate
    if db_info['token_count'] > 0:
        exit_rate = db_info['sold_count'] / db_info['token_count']
        if exit_rate < 0.3:
            risk_factors.append(f"⚠️  Low exit rate ({exit_rate*100:.1f}%) - may be holding bags or testing")
        elif exit_rate > 0.8:
            risk_factors.append(f"✓ High exit rate ({exit_rate*100:.1f}%) - actively trading")

    # Check profitability
    if db_info['avg_profit_pct'] < -50:
        risk_factors.append("⚠️  Negative average returns - possible poor timing or risk management")
    elif db_info['avg_profit_pct'] > 100:
        risk_factors.append("✓ Strong profitability - skilled trader")

    # Check profit
    if db_info['total_profit_usd'] < 0:
        risk_factors.append("⚠️  Net loss on trades - be cautious")
    elif db_info['total_profit_usd'] > 1000:
        risk_factors.append("✓ Significant profits generated")

    if not risk_factors:
        risk_factors.append("✓ No major red flags detected in database records")

    for factor in risk_factors:
        print(f"  {factor}")

    print()

    # What to look for in on-chain data
    print("WHAT TO VERIFY ON-CHAIN:")
    print("-" * 160)
    print("  When you analyze the wallet on Solscan:")
    print("  ✓ Transaction frequency - how active is this wallet?")
    print("  ✓ Fund sources - where do their SOL/funds come from?")
    print("  ✓ Multi-wallet activity - do they control other wallets?")
    print("  ✓ Timing patterns - when do they buy/sell relative to token launches?")
    print("  ✓ Profit extraction - do they cash out immediately or hold?")
    print("  ✓ Suspicious patterns:")
    print("     • Multiple transfers to different wallets (potential funds splitting)")
    print("     • Rapid rapid in/out flows (market making or wash trading)")
    print("     • Large SOL movements before token launches (potential insider funding)")
    print()

    print("DIRECT WALLET LINK:")
    print("-" * 160)
    print(f"  Solscan: https://solscan.io/address/{creator_address}")
    print(f"  Magic Eden: https://magiceden.io/marketplace?search={creator_address}")
    print()

    print(f"{'='*160}\n")


if __name__ == '__main__':
    # Analyze the duplicate creator by default
    creator_to_analyze = "6FCpd6KMKKrPLzgtQEcT3YCzEpwykqT2a6pKp8RpFGaA"
    fetch_all_transactions = False

    if len(sys.argv) > 1:
        creator_to_analyze = sys.argv[1]

    # Check for --full flag to fetch all transactions
    if "--full" in sys.argv or "-f" in sys.argv:
        fetch_all_transactions = True

    # If fetch_all is requested, modify the analyze function to use it
    if fetch_all_transactions:
        import builtins
        original_fetch = fetch_helius_transactions

        def fetch_with_flag(addr, fetch_all=False):
            return original_fetch(addr, fetch_all=True)

        # Temporarily replace the function
        fetch_helius_transactions = fetch_with_flag

    analyze_creator_wallet(creator_to_analyze)
