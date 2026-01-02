#!/bin/bash

# Trading Executor Integration Tests Runner
# This script runs the integration tests with Helius RPC support

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Trading Executor Integration Tests${NC}"
echo -e "${GREEN}================================${NC}"

# Check if HELIUS_API_KEY is set
if [ -z "$HELIUS_API_KEY" ]; then
    echo -e "${YELLOW}⚠️  HELIUS_API_KEY not set${NC}"
    echo -e "${YELLOW}Setting up to use public RPC (may be rate-limited)${NC}"
    echo ""
    echo "To use your Helius API key:"
    echo "  export HELIUS_API_KEY='your_api_key_here'"
    echo ""
else
    echo -e "${GREEN}✓ Using Helius RPC with API key${NC}"
    echo ""
fi

# Run the integration tests
echo "Running integration tests..."
echo ""

python3 -m pytest tests/test_trading_executor_integration.py -v -s

# Print summary
echo ""
echo -e "${GREEN}================================${NC}"
echo "Test run complete!"
echo -e "${GREEN}================================${NC}"
