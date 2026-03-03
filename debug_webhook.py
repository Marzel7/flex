#!/usr/bin/env python3
"""
Webhook debugging script.
Checks:
1. Is ngrok tunnel active?
2. Is Flask running?
3. What's the last webhook timestamp?
4. Are we missing recent transactions?
"""

import sqlite3
import requests
import time
from datetime import datetime, timedelta

DB_PATH = "flex_complete_database.db"

print("=" * 70)
print("WEBHOOK DIAGNOSTIC CHECK")
print("=" * 70)

# 1. Check Flask
print("\n1️⃣ FLASK SERVER")
try:
    r = requests.get("http://localhost:5002/api/webhook-status", timeout=2)
    if r.status_code == 200:
        print("✅ Flask is running on port 5002")
    else:
        print(f"❌ Flask returned status {r.status_code}")
except Exception as e:
    print(f"❌ Flask not responding: {e}")

# 2. Check ngrok
print("\n2️⃣ NGROK TUNNEL")
try:
    r = requests.get("http://localhost:4040/api/tunnels", timeout=2)
    if r.status_code == 200:
        data = r.json()
        tunnels = data.get('tunnels', [])
        if tunnels:
            tunnel = tunnels[0]
            print(f"✅ Ngrok tunnel active")
            print(f"   Public URL: {tunnel['public_url']}")
            print(f"   Target: {tunnel['config']['addr']}")
        else:
            print("❌ No tunnels found")
except Exception as e:
    print(f"❌ Ngrok not responding: {e}")

# 3. Check database
print("\n3️⃣ DATABASE STATUS")
try:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cur = conn.cursor()

    # Total webhooks
    cur.execute("SELECT COUNT(*) FROM webhook_seen_signatures")
    total_sigs = cur.fetchone()[0]
    print(f"✅ Total webhooks received: {total_sigs}")

    # Last webhook
    cur.execute("""
        SELECT first_seen_at FROM webhook_seen_signatures
        ORDER BY first_seen_at DESC LIMIT 1
    """)
    result = cur.fetchone()
    if result:
        print(f"   Last webhook (server time): {result[0]}")

    # Last transfer timestamp
    cur.execute("""
        SELECT MAX(block_time) FROM creator_outgoing_transfers
        WHERE transaction_signature IS NOT NULL
    """)
    last_block = cur.fetchone()[0]

    if last_block:
        last_dt = datetime.utcfromtimestamp(last_block)
        now = datetime.utcfromtimestamp(time.time())
        ago = now - last_dt
        print(f"✅ Last transfer block_time: {last_dt.strftime('%H:%M:%S')}")
        print(f"   That was: {ago.total_seconds()/60:.0f} minutes ago")

    # Total transfers
    cur.execute("SELECT COUNT(*) FROM creator_outgoing_transfers WHERE transaction_signature IS NOT NULL")
    total = cur.fetchone()[0]
    print(f"✅ Total transfers recorded: {total}")

    conn.close()
except Exception as e:
    print(f"❌ Database error: {e}")

# 4. Check for recent activity
print("\n4️⃣ RECENT ACTIVITY (last 5 minutes)")
try:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    now_ts = int(time.time())
    five_min_ago = now_ts - (5 * 60)

    cur.execute("""
        SELECT COUNT(*) FROM webhook_seen_signatures
        WHERE first_seen_at > datetime('now', '-5 minutes')
    """)
    recent_sigs = cur.fetchone()[0]
    print(f"Webhooks in last 5 min: {recent_sigs}")

    cur.execute("""
        SELECT COUNT(*) FROM creator_outgoing_transfers
        WHERE transaction_signature IS NOT NULL
        AND block_time > ?
    """, (five_min_ago,))
    recent_transfers = cur.fetchone()[0]
    print(f"Transfers in last 5 min: {recent_transfers}")

    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")

print("\n" + "=" * 70)
print("TROUBLESHOOTING STEPS:")
print("=" * 70)
print("""
If Last Activity is old:
1. Check if your TX is confirmed on Solana blockchain
2. Verify webhook is registered in Helius dashboard
3. Check webhook URL matches ngrok tunnel
4. Verify accountAddresses in webhook matches your wallet pubkey
5. Wait 30-60 seconds for Helius to process

If ngrok tunnel is disconnected:
1. Restart ngrok: ngrok http 5002
2. Update webhook URL in Helius dashboard
3. Restart Flask: python3 main.py

If Flask is down:
1. Check port 5002: lsof -ti:5002
2. Kill and restart: python3 main.py
3. Check logs: tail -f flask.log
""")
