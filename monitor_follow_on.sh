#!/bin/bash

echo "🔍 Monitoring for FOLLOW_ON_DISCOVERY activity..."
echo "Looking for:"
echo "  1. Starting search (entry point)"
echo "  2. Scanning anchors (which ones)"
echo "  3. Candidates found (extraction)"
echo "  4. Validation results (owner checks)"
echo "  5. Success/exhausted (final state)"
echo ""
echo "Press Ctrl+C to stop"
echo "---"

tail -f listener.log | grep -E "FOLLOW_ON|MIGRATION_DETECTED" | while read line; do
  if [[ $line == *"Starting search"* ]]; then
    echo "🟢 $(date '+%H:%M:%S') - $line"
  elif [[ $line == *"Scanning anchor"* ]]; then
    echo "🔷 $(date '+%H:%M:%S') - $line"
  elif [[ $line == *"Found candidate"* ]]; then
    echo "🟡 $(date '+%H:%M:%S') - $line"
  elif [[ $line == *"Found valid pool"* ]] || [[ $line == *"✅"* ]]; then
    echo "🟦 $(date '+%H:%M:%S') - $line"
  elif [[ $line == *"Exhausted"* ]]; then
    echo "🔴 $(date '+%H:%M:%S') - $line"
  else
    echo "⚪ $(date '+%H:%M:%S') - $line"
  fi
done
