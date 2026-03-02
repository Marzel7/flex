#!/bin/bash
# Manual Cost Testing for creator_outgoing_extractor
# Measures actual Helius credit cost vs instrumented estimate
#
# Usage: bash TEST_CREATOR_OUTGOING_COST.sh
# Time: ~1 hour + 5 minutes for setup/verification

set -e

echo "════════════════════════════════════════════════════════════════════════════════"
echo "HELIUS COST TESTING - creator_outgoing_extractor"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""

# Step 1: Get baseline
echo "STEP 1: Establish Baseline"
echo "────────────────────────────────────────────────────────────────────────────────"
echo ""
echo "Opening Helius dashboard in browser..."
echo "URL: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce"
echo ""
read -p "Press ENTER when you've noted the 'Credits Remaining' number..."
echo ""
read -p "Enter 'Credits Remaining' from dashboard: " BASELINE_REMAINING
read -p "Enter 'Credits Used' from dashboard: " BASELINE_USED
echo ""
echo "Baseline recorded:"
echo "  Credits Remaining: $BASELINE_REMAINING"
echo "  Credits Used: $BASELINE_USED"
echo ""

# Save baseline
python helius_usage_cli.py update "$BASELINE_REMAINING" "$BASELINE_USED" "$BASELINE_USED" 2>/dev/null || true
echo "✅ Baseline saved to database"
echo ""

# Step 2: Clean up processes
echo "STEP 2: Stop all Flex processes"
echo "────────────────────────────────────────────────────────────────────────────────"
pkill -f "pumpfun_curve_listener" 2>/dev/null || true
pkill -f "creator_outgoing_extractor" 2>/dev/null || true
pkill -f "funder_incoming_extractor" 2>/dev/null || true
echo "✅ All Flex processes stopped"
echo ""
echo "Waiting 10 seconds for clean state..."
sleep 10
echo ""

# Step 3: Run the extractor for exactly 1 hour
echo "STEP 3: Run creator_outgoing_extractor for 1 hour"
echo "────────────────────────────────────────────────────────────────────────────────"
echo "Starting: $(date)"
echo "Will run until: $(date -d '+1 hour')"
echo ""

LOG_FILE="/tmp/creator_outgoing_test_$(date +%s).log"
timeout 3600 python creator_outgoing_extractor.py > "$LOG_FILE" 2>&1 || true

echo ""
echo "✅ 1 hour test completed at $(date)"
echo "   Logs saved to: $LOG_FILE"
echo ""

# Step 4: Measure the result
echo "STEP 4: Measure Credit Cost"
echo "────────────────────────────────────────────────────────────────────────────────"
echo ""
echo "Check dashboard for new 'Credits Remaining' number"
echo "URL: https://dashboard.helius.dev/rpcs?projectId=b5b55487-ccfb-43f8-a2fb-766fbb68f8ce"
echo ""
read -p "Press ENTER when you've noted the new 'Credits Remaining' number..."
read -p "Enter new 'Credits Remaining' from dashboard: " FINAL_REMAINING
read -p "Enter new 'Credits Used' from dashboard: " FINAL_USED
echo ""

ACTUAL_COST=$((BASELINE_REMAINING - FINAL_REMAINING))
echo "✅ Actual cost measured:"
echo "   Start: $BASELINE_REMAINING credits"
echo "   End:   $FINAL_REMAINING credits"
echo "   Cost:  $ACTUAL_COST credits consumed"
echo ""

# Save final
python helius_usage_cli.py update "$FINAL_REMAINING" "$FINAL_USED" "$FINAL_USED" 2>/dev/null || true
echo "✅ Final snapshot saved"
echo ""

# Step 5: Analyze the logs
echo "STEP 5: Extract Metrics from Logs"
echo "────────────────────────────────────────────────────────────────────────────────"
echo ""

# Count record_request calls
RECORD_COUNT=$(grep -c "record_request" "$LOG_FILE" 2>/dev/null || echo "0")
echo "Calls recorded in logs: $RECORD_COUNT"
echo ""

# Count getSignaturesForAddress specifically
GETSIG_COUNT=$(grep -c "getSignaturesForAddress" "$LOG_FILE" 2>/dev/null || echo "0")
echo "getSignaturesForAddress calls: $GETSIG_COUNT"
echo ""

# Expected cost (assuming 10 credits per call)
EXPECTED_COST=$((GETSIG_COUNT * 10))
echo "Expected cost (if 10 credits each): $EXPECTED_COST credits"
echo ""

# Step 6: Calculate the ratio
echo "STEP 6: Analyze Results"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""
echo "SUMMARY"
echo "────────────────────────────────────────────────────────────────────────────────"
echo "Actual Helius Cost:        $ACTUAL_COST credits"
echo "Instrumented Calls:        $GETSIG_COUNT × getSignaturesForAddress"
echo "Expected Cost (if 10ea):   $EXPECTED_COST credits"
echo ""

if [ $GETSIG_COUNT -gt 0 ]; then
    ACTUAL_PER_CALL=$(echo "scale=2; $ACTUAL_COST / $GETSIG_COUNT" | bc)
    RATIO=$(echo "scale=1; $ACTUAL_COST / $EXPECTED_COST" | bc)

    echo "CALCULATIONS"
    echo "────────────────────────────────────────────────────────────────────────────────"
    echo "Actual cost per call:      $ACTUAL_PER_CALL credits"
    echo "Ratio (Actual/Expected):   $RATIO"
    echo ""

    echo "INTERPRETATION"
    echo "────────────────────────────────────────────────────────────────────────────────"
    if (( $(echo "$RATIO > 0.9 && $RATIO < 1.1" | bc -l) )); then
        echo "✅ RATIO ≈ 1.0: Cost schedule is CORRECT"
        echo "   getSignaturesForAddress costs 10 credits"
    elif (( $(echo "$RATIO > 9" | bc -l) )); then
        echo "⚠️  RATIO ≈ 10: Calls cost 10x LESS than expected"
        echo "   Actual: ~$ACTUAL_PER_CALL credits per call"
        echo "   Expected: 10 credits per call"
        echo "   → Update CREDIT_SCHEDULE: getSignaturesForAddress = $ACTUAL_PER_CALL"
    elif (( $(echo "$RATIO < 0.15" | bc -l) )); then
        echo "⚠️  RATIO ≈ 0.1: Calls cost 10x MORE than expected"
        echo "   Actual: ~$ACTUAL_PER_CALL credits per call"
        echo "   Expected: 10 credits per call"
        echo "   → Update CREDIT_SCHEDULE: getSignaturesForAddress = $ACTUAL_PER_CALL"
    elif (( $(echo "$RATIO < 0.01" | bc -l) )); then
        echo "❌ RATIO ≈ 0: Calls aren't consuming credits"
        echo "   Possible: Test mode, mock calls, or free tier limits"
        echo "   Check: Are calls actually executing? Check logs for errors"
    else
        echo "⚠️  RATIO = $RATIO: Unexpected ratio"
        echo "   Actual: ~$ACTUAL_PER_CALL credits per call"
        echo "   Investigate: Check logs for errors or unexpected behavior"
    fi
else
    echo "❌ NO CALLS RECORDED"
    echo "   Check logs: $LOG_FILE"
    echo "   Possible: Process didn't run, or calls failed"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════════════════"
echo "TEST COMPLETE"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Review the ratio above"
echo "  2. If ratio ≠ 1.0, update CREDIT_SCHEDULE in rpc_metrics_recorder.py"
echo "  3. Run again to verify"
echo ""
echo "Log file: $LOG_FILE"
echo ""
