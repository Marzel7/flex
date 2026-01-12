#!/usr/bin/env python3
"""
Detect volume bot usage by analyzing creator transaction patterns.

Bot accounts generate artificial volume by:
1. Receiving SOL from coordinated funding sources
2. Creating multiple small buy transactions (accumulation)
3. Dump holdings when price pumps (rug pull execution)

This module identifies:
- Creators using known bot accounts (by name matching)
- Transaction patterns consistent with bot activity
- Links between creator funding and bot execution
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import json
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


# Known bot account patterns
KNOWN_BOTS = {
    'FMHDHLuQERr5FpDPgqSRCPMe9UGC9znz4viYPiVimkcH': {
        'name': 'boostlegends-volumebot',
        'emoji': '🚀',
        'confidence': 'CONFIRMED'
    },
    'FJGSVShEbfLqyVJSABACJQgimZMdK1T3oiNyQwAxvoix': {
        'name': 'boostlegends-volumebot',
        'emoji': '⚡',
        'confidence': 'CONFIRMED'
    }
}

# Bot activity signatures to search for in transaction descriptions
BOT_SIGNATURES = [
    'volumebot',
    'volume-bot',
    'volume_bot',
    'boostlegends',
    'boost-legends',
    'bot-farm',
    'botfarm',
    'pump-bot',
    'pumpbot',
    'dump-bot',
    'dumpbot'
]


def analyze_helius_transaction_type(transaction):
    """
    Analyze a Helius transaction to identify bot-related activity.

    Returns:
    - transaction_type: 'swap', 'transfer', 'unknown', etc.
    - involved_accounts: list of all accounts in transaction
    - description: human-readable transaction description
    """
    if not isinstance(transaction, dict):
        return None

    tx_type = transaction.get('type', 'unknown').lower()
    description = transaction.get('description', '').lower()

    # Extract involved accounts from various fields
    involved = set()

    # From nativeTransfers
    for transfer in transaction.get('nativeTransfers', []):
        if transfer.get('fromUserAccount'):
            involved.add(transfer['fromUserAccount'])
        if transfer.get('toUserAccount'):
            involved.add(transfer['toUserAccount'])

    # From tokenTransfers
    for transfer in transaction.get('tokenTransfers', []):
        if transfer.get('fromUserAccount'):
            involved.add(transfer['fromUserAccount'])
        if transfer.get('toUserAccount'):
            involved.add(transfer['toUserAccount'])

    return {
        'type': tx_type,
        'description': description,
        'involved_accounts': list(involved),
        'timestamp': transaction.get('timestamp'),
        'signature': transaction.get('signature')
    }


def detect_bot_in_transaction(transaction, creator_address):
    """
    Check if a transaction involves a known bot account interacting with a creator.

    Returns: List of bot accounts found, or None
    """
    analysis = analyze_helius_transaction_type(transaction)
    if not analysis:
        return None

    involved = analysis['involved_accounts']
    description = analysis['description']

    found_bots = []

    # Check if any known bot is involved in this transaction
    for bot_address in KNOWN_BOTS.keys():
        if bot_address in involved:
            found_bots.append({
                'bot': bot_address,
                'bot_info': KNOWN_BOTS[bot_address],
                'tx_type': analysis['type'],
                'timestamp': analysis['timestamp'],
                'signature': analysis['signature']
            })

    # Check for bot signatures in description
    for signature in BOT_SIGNATURES:
        if signature in description:
            # Extract addresses from description as potential bot identifiers
            found_bots.append({
                'bot': 'UNKNOWN_BOT',
                'signature': signature,
                'tx_type': analysis['type'],
                'timestamp': analysis['timestamp'],
                'description': description,
                'involved': involved
            })
            break  # Only report once per transaction

    return found_bots if found_bots else None


def get_creator_helius_transactions(creator_address, fetch_all=False):
    """
    Fetch creator's Helius transaction history.

    Returns: List of transactions or None
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
            "limit": 100
        }

        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data
        return None
    except:
        return None


def analyze_creator_for_bot_usage(creator_address):
    """
    Comprehensive analysis of a creator to detect bot usage.

    Returns:
    {
        'creator': address,
        'bot_usage_detected': bool,
        'bot_accounts': [
            {
                'address': bot_address,
                'name': bot_name,
                'transaction_count': number,
                'transaction_types': [...],
                'timestamps': [...],
                'confidence': 'HIGH/MEDIUM/LOW'
            }
        ],
        'pattern_summary': description
    }
    """
    print(f"\n🔍 Analyzing {creator_address[:16]}... for bot usage...")

    # Fetch Helius transactions
    transactions = get_creator_helius_transactions(creator_address, fetch_all=False)

    if not transactions:
        print(f"  ⚠️  No transaction history available")
        return None

    print(f"  ✓ Fetched {len(transactions)} transactions")

    # Scan for bot involvement
    bot_usage = {}

    for tx in transactions:
        found_bots = detect_bot_in_transaction(tx, creator_address)
        if found_bots:
            for bot_info in found_bots:
                bot_key = bot_info.get('bot', 'UNKNOWN')
                if bot_key not in bot_usage:
                    bot_usage[bot_key] = {
                        'address': bot_key,
                        'name': bot_info.get('bot_info', {}).get('name', 'Unknown Bot'),
                        'emoji': bot_info.get('bot_info', {}).get('emoji', ''),
                        'transactions': [],
                        'types': set()
                    }

                bot_usage[bot_key]['transactions'].append({
                    'timestamp': bot_info.get('timestamp'),
                    'signature': bot_info.get('signature'),
                    'type': bot_info.get('tx_type')
                })
                bot_usage[bot_key]['types'].add(bot_info.get('tx_type'))

    if bot_usage:
        print(f"  🚨 FOUND BOT USAGE:")
        for bot_key, info in bot_usage.items():
            tx_count = len(info['transactions'])
            print(f"     - {info['emoji']} {info['name']} ({tx_count} transactions)")

        return {
            'creator': creator_address,
            'bot_usage_detected': True,
            'bot_accounts': [
                {
                    'address': info['address'],
                    'name': info['name'],
                    'emoji': info['emoji'],
                    'transaction_count': len(info['transactions']),
                    'transaction_types': list(info['types']),
                    'confidence': 'HIGH'
                }
                for info in bot_usage.values()
            ],
            'pattern_summary': f"Uses {len(bot_usage)} bot account(s)"
        }
    else:
        return {
            'creator': creator_address,
            'bot_usage_detected': False,
            'pattern_summary': 'No bot usage detected'
        }


def create_bot_detection_table(db_path):
    """
    Create tables for tracking bot usage patterns.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    c = conn.cursor()

    # Table for bot accounts
    c.execute('''
        CREATE TABLE IF NOT EXISTS known_bot_accounts (
            bot_address TEXT PRIMARY KEY,
            bot_name TEXT,
            bot_emoji TEXT,
            confidence TEXT,
            first_detected TIMESTAMP,
            last_updated TIMESTAMP
        )
    ''')

    # Table for creator-bot relationships
    c.execute('''
        CREATE TABLE IF NOT EXISTS creator_bot_usage (
            creator_address TEXT,
            bot_address TEXT,
            transaction_count INTEGER,
            first_transaction TIMESTAMP,
            last_transaction TIMESTAMP,
            transaction_types TEXT,  -- JSON array
            detection_confidence TEXT,
            detected_timestamp TIMESTAMP,
            PRIMARY KEY (creator_address, bot_address)
        )
    ''')

    # Table for bot activity patterns
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_activity_patterns (
            bot_address TEXT,
            pattern_type TEXT,  -- 'accumulation', 'dump', 'coordination', etc.
            creator_address TEXT,
            activity_timestamp TIMESTAMP,
            description TEXT,
            confidence TEXT
        )
    ''')

    conn.commit()
    conn.close()


def register_bot_accounts(db_path):
    """
    Register known bot accounts in the database.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    c = conn.cursor()

    now = datetime.now()

    for bot_address, bot_info in KNOWN_BOTS.items():
        c.execute('''
            INSERT OR REPLACE INTO known_bot_accounts (
                bot_address, bot_name, bot_emoji, confidence,
                first_detected, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            bot_address,
            bot_info['name'],
            bot_info['emoji'],
            bot_info['confidence'],
            now,
            now
        ))

    conn.commit()
    conn.close()


def store_bot_usage(db_path, creator_address, analysis):
    """
    Store bot usage analysis results in database.
    """
    if not analysis or not analysis.get('bot_usage_detected'):
        return False

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    c = conn.cursor()

    try:
        now = datetime.now()

        for bot_info in analysis.get('bot_accounts', []):
            c.execute('''
                INSERT OR REPLACE INTO creator_bot_usage (
                    creator_address, bot_address, transaction_count,
                    first_transaction, last_transaction, transaction_types,
                    detection_confidence, detected_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                creator_address,
                bot_info['address'],
                bot_info['transaction_count'],
                datetime.fromtimestamp(bot_info['transaction_count']),  # Placeholder
                datetime.fromtimestamp(bot_info['transaction_count']),  # Placeholder
                json.dumps(bot_info.get('transaction_types', [])),
                bot_info.get('confidence', 'MEDIUM'),
                now
            ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  ⚠️  Error storing bot usage: {e}")
        return False


def analyze_all_critical_creators(db_path):
    """
    Analyze all CRITICAL/HIGH risk creators for bot usage.
    """
    print("="*100)
    print("BOT USAGE ANALYSIS FOR CRITICAL CREATORS")
    print("="*100)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    c = conn.cursor()

    # Create bot detection tables
    create_bot_detection_table(db_path)
    register_bot_accounts(db_path)

    # Get all CRITICAL/HIGH creators
    c.execute('''
        SELECT DISTINCT pumpfun_creator, funding_risk_level
        FROM pools
        WHERE funding_risk_level IN ('CRITICAL', 'HIGH')
        ORDER BY funding_risk_level DESC
    ''')

    critical_creators = c.fetchall()
    conn.close()

    print(f"\nAnalyzing {len(critical_creators)} CRITICAL/HIGH risk creators\n")

    bot_using_creators = []

    for i, (creator, risk_level) in enumerate(critical_creators, 1):
        analysis = analyze_creator_for_bot_usage(creator)

        if analysis and analysis.get('bot_usage_detected'):
            bot_using_creators.append({
                'creator': creator,
                'risk_level': risk_level,
                'analysis': analysis
            })

            # Store in database
            store_bot_usage(db_path, creator, analysis)

    # Summary
    print(f"\n\n" + "="*100)
    print(f"BOT USAGE SUMMARY")
    print("="*100)
    print(f"\nTotal CRITICAL/HIGH creators: {len(critical_creators)}")
    print(f"Creators using bots: {len(bot_using_creators)}\n")

    if bot_using_creators:
        for item in bot_using_creators:
            creator = item['creator']
            analysis = item['analysis']
            print(f"🚨 {creator[:16]}... (Risk: {item['risk_level']})")
            for bot in analysis['bot_accounts']:
                print(f"   Uses: {bot['emoji']} {bot['name']}")
                print(f"   Transactions: {bot['transaction_count']}")
            print()

    return bot_using_creators


if __name__ == '__main__':
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Database not found")
        sys.exit(1)

    # Run analysis
    results = analyze_all_critical_creators(db_path)
    sys.exit(0 if results else 1)
