#!/usr/bin/env python3
"""
Real-time bot detection integration for WebSocket listener.

When a new token is detected, this checks if the creator uses known bot accounts.
If bot usage is found, the token is immediately flagged as a confirmed pump-and-dump.

Usage:
    In test_pumpswap_listener.py, call:

    bot_flag = check_creator_for_bot_usage(creator_address)
    if bot_flag:
        risk_level = 'LOW+'  # 🟢 Green - Confirmed bot usage
        # Block trading, show warning, etc.
"""

import sqlite3
from pathlib import Path
import os
import sys

# Load environment
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

# Known bot accounts
KNOWN_BOTS = {
    'FMHDHLuQERr5FpDPgqSRCPMe9UGC9znz4viYPiVimkcH': 'boostlegends-volumebot 🚀',
    'FJGSVShEbfLqyVJSABACJQgimZMdK1T3oiNyQwAxvoix': 'boostlegends-volumebot ⚡'
}

BOT_SIGNATURES = [
    'volumebot', 'volume-bot', 'volume_bot',
    'boostlegends', 'boost-legends',
    'pump-bot', 'pumpbot'
]


def get_creator_helius_transactions(creator_address, max_tx=50):
    """
    Fetch creator's recent Helius transactions.

    Args:
        creator_address: Creator wallet address
        max_tx: Maximum transactions to fetch (default: 50, max effective: 100 per API call)

    Returns:
        List of transactions or None
    """
    if not HAS_REQUESTS:
        return None

    api_key = os.getenv('HELIUS_API_KEY')
    if not api_key:
        return None

    try:
        url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator_address}/transactions"
        params = {
            "api-key": api_key,
            "limit": min(max_tx, 100)  # API limit is 100
        }

        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data[:max_tx]
        return None
    except Exception as e:
        return None


def check_transaction_for_bots(transaction):
    """
    Check if a transaction involves known bot accounts.

    Returns:
        List of found bots or None
    """
    if not isinstance(transaction, dict):
        return None

    found = []

    # Check all accounts in transaction
    all_accounts = set()

    # Native transfers
    for transfer in transaction.get('nativeTransfers', []):
        if transfer.get('fromUserAccount'):
            all_accounts.add(transfer['fromUserAccount'])
        if transfer.get('toUserAccount'):
            all_accounts.add(transfer['toUserAccount'])

    # Token transfers
    for transfer in transaction.get('tokenTransfers', []):
        if transfer.get('fromUserAccount'):
            all_accounts.add(transfer['fromUserAccount'])
        if transfer.get('toUserAccount'):
            all_accounts.add(transfer['toUserAccount'])

    # Check for known bots
    for bot_address in KNOWN_BOTS.keys():
        if bot_address in all_accounts:
            found.append({
                'bot': bot_address,
                'name': KNOWN_BOTS[bot_address],
                'tx_sig': transaction.get('signature')
            })

    # Check for bot signatures in description
    description = transaction.get('description', '').lower()
    for sig in BOT_SIGNATURES:
        if sig in description and not found:  # Only add if not already found
            found.append({
                'bot': 'UNKNOWN_BOT_SIGNATURE',
                'name': f'Unknown bot ({sig})',
                'tx_sig': transaction.get('signature')
            })
            break

    return found if found else None


def calculate_bot_activity_level(total_bot_transactions):
    """
    Categorize bot activity severity based on transaction count.

    Args:
        total_bot_transactions: Sum of all bot transactions

    Returns:
        'NONE', 'LOW' (1-5), 'MEDIUM' (6-20), or 'HIGH' (21+)
    """
    if total_bot_transactions == 0:
        return 'NONE'
    elif total_bot_transactions <= 5:
        return 'LOW'
    elif total_bot_transactions <= 20:
        return 'MEDIUM'
    else:
        return 'HIGH'


def check_creator_for_bot_usage(creator_address, quick=True, db_path=None):
    """
    Fast check if a creator uses known bot accounts.

    Args:
        creator_address: Creator wallet address
        quick: If True, check database first (fast), then do Helius scan
               If False, only do Helius scan
        db_path: Optional path to database file. If not provided, looks in current directory.

    Returns:
        {
            'detected': True/False,
            'bots': [{'bot': address, 'name': name, 'tx_count': count}],
            'confidence': 'HIGH/MEDIUM/LOW',
            'risk_verdict': 'LOW+' or None
        }
    """

    if not db_path:
        db_path = Path(__file__).parent / 'pumpswap_tokens.db'
    else:
        db_path = Path(db_path)

    # Quick database check first
    if quick and db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            c = conn.cursor()

            c.execute('''
                SELECT bot_address, transaction_count
                FROM creator_bot_usage
                WHERE creator_address = ?
            ''', (creator_address,))

            results = c.fetchall()
            conn.close()

            if results:
                total_tx = sum(tx_count for _, tx_count in results)
                return {
                    'detected': True,
                    'bots': [
                        {
                            'bot': bot,
                            'name': KNOWN_BOTS.get(bot, f'Bot: {bot[:8]}...'),
                            'tx_count': tx_count
                        }
                        for bot, tx_count in results
                    ],
                    'confidence': 'HIGH',
                    'risk_verdict': 'LOW+',  # 🟢 Green - Bot detected
                    'bot_activity_level': calculate_bot_activity_level(total_tx)
                }
        except:
            pass  # Fall through to Helius scan

    # Helius real-time scan
    transactions = get_creator_helius_transactions(creator_address, max_tx=100)

    if not transactions:
        return {
            'detected': False,
            'bots': [],
            'confidence': 'LOW',
            'risk_verdict': None,
            'bot_activity_level': 'NONE'
        }

    # Scan transactions for bot activity
    bot_accounts = {}

    for tx in transactions:
        found_bots = check_transaction_for_bots(tx)
        if found_bots:
            for bot_info in found_bots:
                bot = bot_info['bot']
                if bot not in bot_accounts:
                    bot_accounts[bot] = {
                        'name': bot_info['name'],
                        'count': 0
                    }
                bot_accounts[bot]['count'] += 1

    if bot_accounts:
        total_tx = sum(info['count'] for info in bot_accounts.values())
        return {
            'detected': True,
            'bots': [
                {
                    'bot': bot,
                    'name': info['name'],
                    'tx_count': info['count']
                }
                for bot, info in bot_accounts.items()
            ],
            'confidence': 'HIGH',
            'risk_verdict': 'LOW+',  # 🟢 Green - Bot detected
            'bot_activity_level': calculate_bot_activity_level(total_tx)
        }

    return {
        'detected': False,
        'bots': [],
        'confidence': 'NONE',
        'risk_verdict': None,
        'bot_activity_level': 'NONE'
    }


def store_bot_detection_result(creator_address, result, db_path=None):
    """
    Store bot detection result in database for future quick lookup.

    Args:
        creator_address: Creator wallet address
        result: Result from check_creator_for_bot_usage()
        db_path: Path to database (default: pumpswap_tokens.db)
    """
    if not db_path:
        db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not result['detected']:
        return False

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        c = conn.cursor()

        from datetime import datetime
        now = datetime.now()

        for bot_info in result['bots']:
            c.execute('''
                INSERT OR REPLACE INTO creator_bot_usage (
                    creator_address, bot_address, transaction_count,
                    detection_confidence, detected_timestamp
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                creator_address,
                bot_info['bot'],
                bot_info['tx_count'],
                result['confidence'],
                now
            ))

        # Also flag the creator's tokens
        c.execute('''
            UPDATE pools
            SET bot_detection_flag = 'BOOSTLEGENDS_VOLUMEBOT'
            WHERE pumpfun_creator = ?
        ''', (creator_address,))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[BOT_DETECTION] ⚠ Error storing bot result: {str(e)[:60]}")
        return False


# Example usage in WebSocket listener:
if __name__ == '__main__':
    # Test on a known bot-using creator
    test_creator = 'HXuuXP9ZNn7WGaKKqiAcX5473TeyXb91afB7mKkRvCF8'

    print(f"Testing bot detection on {test_creator[:16]}...")
    result = check_creator_for_bot_usage(test_creator, quick=True)

    print(f"\nDetected: {result['detected']}")
    print(f"Confidence: {result['confidence']}")
    print(f"Verdict: {result['risk_verdict']}")

    if result['bots']:
        print("\nBots found:")
        for bot in result['bots']:
            print(f"  - {bot['name']} ({bot['tx_count']} transactions)")
