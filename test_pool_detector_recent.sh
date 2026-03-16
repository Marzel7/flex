#!/bin/bash
# Test pool detector against recent tokens from the database

set -e

echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║ Pool Detector Test - Recent Tokens                                ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

# Get the most recent token
MINT=$(sqlite3 database/flex_complete_database.db \
  "SELECT mint FROM tracked_tokens WHERE created_at IS NOT NULL ORDER BY created_at DESC LIMIT 1;")

if [ -z "$MINT" ]; then
  echo "❌ No tokens found in database"
  exit 1
fi

echo "Testing with most recent token:"
echo "Token: $MINT"
echo ""

# Check if verbose flag is set
VERBOSE_FLAG=""
if [ "$1" == "-v" ] || [ "$1" == "--verbose" ]; then
  VERBOSE_FLAG="--verbose"
  echo "Running with verbose debug output..."
  echo ""
fi

# Run the test
python test_pool_detector_integration.py \
  --mint "$MINT" \
  --rpc https://api.mainnet-beta.solana.com \
  $VERBOSE_FLAG

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo ""
  echo "✅ Test passed - pool was successfully detected"
else
  echo ""
  echo "❌ Test failed - pool detection returned None"
  echo ""
  echo "Next steps:"
  echo "  1. Check the listener logs: tail -f /tmp/listener.log"
  echo "  2. Run with verbose mode: ./test_pool_detector_recent.sh -v"
  echo "  3. Check database: sqlite3 database/flex_complete_database.db"
  echo "     SELECT mint, pair_address FROM tracked_tokens WHERE mint = '$MINT';"
fi

exit $EXIT_CODE
