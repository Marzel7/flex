#!/bin/bash

echo "📊 Follow-On Discovery Status Check"
echo "===================================="
echo ""

echo "1️⃣  Total tokens in resolution telemetry:"
sqlite3 database/flex_complete_database.db "SELECT COUNT(*) FROM token_resolution_telemetry"

echo ""
echo "2️⃣  Resolution sources breakdown:"
sqlite3 database/flex_complete_database.db "SELECT resolve_source, COUNT(*) as count FROM token_resolution_telemetry GROUP BY resolve_source ORDER BY count DESC"

echo ""
echo "3️⃣  Tokens with follow-on discoveries:"
sqlite3 database/flex_complete_database.db "SELECT COUNT(*) FROM token_resolution_telemetry WHERE resolve_source = 'follow_on'"

echo ""
echo "4️⃣  Most recent tokens (check what's happening):"
sqlite3 database/flex_complete_database.db "SELECT mint, resolve_source, resolve_seconds FROM token_resolution_telemetry ORDER BY resolved_at DESC LIMIT 5"

echo ""
echo "5️⃣  Recent listener log entries:"
echo "Last 50 FOLLOW_ON lines from listener.log:"
tail -n 1000 listener.log | grep "FOLLOW_ON" | tail -50

echo ""
echo "Done!"
