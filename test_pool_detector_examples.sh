#!/bin/bash
# Quick reference for testing pool detector with real tokens

echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║ Pool Detector Integration Test Examples                           ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

# Test against a specific signature (you'll need a real one)
echo "1. Test with a specific migration signature:"
echo "   python test_pool_detector_integration.py --signature <tx_signature>"
echo ""

# Test with a token mint
echo "2. Test with a token mint:"
echo "   python test_pool_detector_integration.py --mint HfYTqP8ecb5XyW4aYMULMHLrvseNW3nWBrvgSTJ3pump"
echo ""

# Test with verbose output
echo "3. Test with verbose debug logging:"
echo "   python test_pool_detector_integration.py --signature <sig> --verbose"
echo ""

# Test with custom RPC
echo "4. Test with custom RPC endpoint:"
echo "   python test_pool_detector_integration.py --signature <sig> --rpc https://mainnet.helius-rpc.com"
echo ""

echo "Expected successful output:"
echo "   ✅ Pool Found: <pool_address>"
echo ""

echo "Getting a signature:"
echo "   1. Launch a token on pump.fun"
echo "   2. Find the migration tx signature in your listener logs"
echo "   3. Pass it to the test"
echo ""

echo "Or use a recent token from the database:"
echo "   sqlite3 database/flex_complete_database.db"
echo "   SELECT mint FROM tracked_tokens ORDER BY created_at DESC LIMIT 1;"
echo ""
