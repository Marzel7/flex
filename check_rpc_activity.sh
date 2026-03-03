#!/bin/bash
# Quick RPC activity checker - run this periodically to see what's happening

DB_PATH="flex_complete_database.db"
LOG_DIR="rpc_monitor_logs"

mkdir -p "$LOG_DIR"

# Get current timestamp
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
TIMESTAMP_FILE=$(date +"%Y%m%d_%H%M%S")

echo "=== RPC Activity Report - $TIMESTAMP ===" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
echo "" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"

# Overall summary
echo "📊 OVERALL SUMMARY" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
sqlite3 "$DB_PATH" "
SELECT
  'Total RPC Calls' as metric,
  COUNT(*) as value
FROM rpc_metrics
UNION ALL
SELECT
  'Total Credits',
  SUM(credits)
FROM rpc_metrics
UNION ALL
SELECT
  'Unique Methods',
  COUNT(DISTINCT method)
FROM rpc_metrics
UNION ALL
SELECT
  'Unique Sources',
  COUNT(DISTINCT source_file)
FROM rpc_metrics;
" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"

echo "" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
echo "📈 CREDITS BY METHOD (Last 1 hour)" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
sqlite3 "$DB_PATH" "
SELECT
  method,
  COUNT(*) as calls,
  SUM(credits) as total_credits
FROM rpc_metrics
WHERE recorded_at > datetime('now', '-1 hour')
GROUP BY method
ORDER BY total_credits DESC
LIMIT 10;
" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"

echo "" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
echo "🔍 CREDITS BY SOURCE (Last 1 hour)" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
sqlite3 "$DB_PATH" "
SELECT
  COALESCE(source_file, 'unknown') as source,
  COUNT(*) as calls,
  SUM(credits) as total_credits
FROM rpc_metrics
WHERE recorded_at > datetime('now', '-1 hour')
GROUP BY source_file
ORDER BY total_credits DESC;
" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"

echo "" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
echo "⏱️  RECENT ACTIVITY (Last 30 seconds)" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
sqlite3 "$DB_PATH" "
SELECT
  strftime('%H:%M:%S', recorded_at) as time,
  method,
  source_file,
  COUNT(*) as calls,
  SUM(credits) as credits
FROM rpc_metrics
WHERE recorded_at > datetime('now', '-30 seconds')
GROUP BY time, method, source_file
ORDER BY recorded_at DESC;
" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"

echo "" | tee -a "$LOG_DIR/activity_${TIMESTAMP_FILE}.log"
echo "✅ Report saved to: $LOG_DIR/activity_${TIMESTAMP_FILE}.log"
