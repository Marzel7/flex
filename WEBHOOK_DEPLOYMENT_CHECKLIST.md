# FLEX Webhook M5 Deployment Checklist

## Pre-Deployment

- [ ] Review WEBHOOK_ARCHITECTURE_M5.md
- [ ] Review WEBHOOK_INTEGRATION_GUIDE.md
- [ ] Verify Flask app is running
- [ ] Verify ngrok tunnel is active (if using)
- [ ] Verify Helius webhook endpoint exists

## File Setup

- [ ] Copy `webhook_handler.py` to FLEX directory
- [ ] Copy `webhook_worker.py` to FLEX directory
- [ ] Copy `webhook_integration.py` to FLEX directory
- [ ] Copy `sql_webhook_schema.sql` to FLEX directory
- [ ] Verify all 4 files are readable

## Database Setup

- [ ] Run: `sqlite3 flex_complete_database.db < sql_webhook_schema.sql`
- [ ] Verify tables created:
  ```bash
  sqlite3 flex_complete_database.db "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('sol_transfers', 'address_activity', 'work_queue')"
  ```
- [ ] Should show 3 tables

## Flask Integration

- [ ] Open `main.py`
- [ ] Add import at top:
  ```python
  from webhook_integration import init_webhook_system
  ```
- [ ] Find Flask app initialization section
- [ ] Add after `app = Flask(__name__)`:
  ```python
  # Initialize webhook system
  init_webhook_system(app)
  ```
- [ ] Verify no syntax errors: `python3 -m py_compile main.py`

## Environment Variables (Optional)

- [ ] Optional - set webhook auth:
  ```bash
  export HELIUS_WEBHOOK_AUTH="Bearer your-secret-key"
  ```
- [ ] Optional - set database path:
  ```bash
  export FLEX_DB_PATH="flex_complete_database.db"
  ```

## Flask Restart

- [ ] Stop Flask: `Ctrl+C` or `pkill -f "python3 main.py"`
- [ ] Wait 2 seconds for port to release
- [ ] Start Flask: `python3 main.py > flask.log 2>&1 &`
- [ ] Verify Flask started: `ps aux | grep main.py | grep -v grep`
- [ ] Check logs for errors: `tail -20 flask.log`

## Verify Routes

- [ ] Test webhook endpoint:
  ```bash
  curl -X POST http://localhost:5002/helius/webhook \
    -H "Content-Type: application/json" \
    -d '[{"signature":"test","slot":0,"blockTime":0,"transaction":{"message":{"accountKeys":[{"pubkey":"5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ"},{"pubkey":"HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z"},{"pubkey":"11111111111111111111111111111111"}],"instructions":[{"programIdIndex":2,"parsed":{"type":"transfer","info":{"source":"5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ","destination":"HZUZfV5SYyEtDvaSp6UTLmj9Vw628HEMgR489sGPJ23z","lamports":200000}}}]},"signatures":["test"],"meta":{"err":null,"preBalances":[1000000000,500000000,0],"postBalances":[999920000,500200000,0]}}]'
  ```
  Should return: `ok` with 200 status

- [ ] Test health endpoint:
  ```bash
  curl http://localhost:5002/api/webhook/status | jq
  ```
  Should show JSON with transfer counts

## Database Verification

- [ ] Check sol_transfers table:
  ```bash
  sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM sol_transfers"
  ```
  Should show >= 1 (from test above)

- [ ] Check address_activity table:
  ```bash
  sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM address_activity"
  ```
  Should show >= 2 (source + destination)

- [ ] Check work_queue table:
  ```bash
  sqlite3 flex_complete_database.db "SELECT address, priority FROM work_queue ORDER BY priority DESC LIMIT 5"
  ```
  Should show queued addresses with priority

## Monitor Logs

- [ ] Open log stream:
  ```bash
  tail -f flask.log | grep -E "WEBHOOK|WORKER"
  ```
- [ ] Watch for [WEBHOOK] messages (should see test webhook)
- [ ] Watch for [WORKER] messages (worker should be processing)
- [ ] No [ERROR] messages

## Helius Configuration

- [ ] Log into Helius dashboard
- [ ] Go to Webhooks section
- [ ] Find your webhook configuration
- [ ] Update URL to:
  ```
  https://your-ngrok-url/helius/webhook
  ```
  OR
  ```
  https://your-domain/helius/webhook
  ```
- [ ] Verify webhook status shows "Active" or "Connected"
- [ ] Verify transaction types includes "ANY" or "TRANSFER"
- [ ] Verify account addresses includes your wallet: `5ZpgwwHAxs5kuer3dwwJQxjxvWaXHaLvchZJCRqigPtJ`

## Production Verification

- [ ] Send real transaction from your wallet
- [ ] Wait 10-30 seconds
- [ ] Check logs:
  ```bash
  tail -50 flask.log | grep WEBHOOK
  ```
  Should show: "STORED: " with transfer details

- [ ] Check database:
  ```bash
  sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM sol_transfers WHERE received_at > datetime('now', '-1 minute')"
  ```
  Should show 1+ transfers from last minute

- [ ] Check dashboard/API:
  ```bash
  curl http://localhost:5002/api/webhook/status | jq '.recent_transfers[0]'
  ```
  Should show your transaction at top

## Performance Baseline

- [ ] Record: How many transfers before/after
  ```bash
  sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM sol_transfers"
  ```

- [ ] Record: Queue size
  ```bash
  sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM work_queue"
  ```

- [ ] Record: Active addresses
  ```bash
  sqlite3 flex_complete_database.db "SELECT COUNT(*) FROM address_activity"
  ```

## Monitoring Setup

- [ ] Monitor logs continuously:
  ```bash
  tail -f flask.log | grep -E "WEBHOOK|WORKER"
  ```

- [ ] Set up log rotation (optional):
  ```bash
  # Add to crontab for daily rotation
  0 0 * * * mv flask.log flask.log.$(date +\%Y\%m\%d) && python3 main.py > flask.log 2>&1 &
  ```

- [ ] Check health periodically:
  ```bash
  watch -n 5 'curl -s http://localhost:5002/api/webhook/status | jq'
  ```

## Troubleshooting

If webhooks aren't arriving:

- [ ] Check ngrok tunnel:
  ```bash
  curl http://localhost:4040/api/tunnels | jq '.tunnels[0].public_url'
  ```

- [ ] Verify Helius dashboard webhook URL matches above

- [ ] Check Flask is listening:
  ```bash
  lsof -ti:5002 | xargs ps -p
  ```

- [ ] Check logs for errors:
  ```bash
  tail -100 flask.log | grep -i error
  ```

If database operations are slow:

- [ ] Check WAL files:
  ```bash
  ls -lh flex_complete_database.db*
  ```

- [ ] Check if database is locked:
  ```bash
  sqlite3 flex_complete_database.db ".timeout 1000" "SELECT COUNT(*) FROM sol_transfers"
  ```

## Post-Deployment

- [ ] Document any custom settings applied
- [ ] Note baseline performance metrics
- [ ] Set up monitoring alerts (if applicable)
- [ ] Schedule periodic log review
- [ ] Plan for scaling decisions (if needed)

## Success Criteria

✅ All items checked off
✅ Test webhook returned 200
✅ Health endpoint returns JSON
✅ sol_transfers table has rows
✅ address_activity table has rows
✅ work_queue table has rows
✅ Real transaction appears in database
✅ [WEBHOOK] messages in logs
✅ [WORKER] messages in logs
✅ No [ERROR] messages

## Rollback Plan

If issues occur:

1. Stop Flask: `pkill -f "python3 main.py"`
2. Undo main.py changes (remove `init_webhook_system` call)
3. Remove webhook tables (optional):
   ```bash
   sqlite3 flex_complete_database.db << 'EOF'
   DROP TABLE IF EXISTS sol_transfers;
   DROP TABLE IF EXISTS address_activity;
   DROP TABLE IF EXISTS work_queue;
   EOF
   ```
4. Restart Flask: `python3 main.py > flask.log 2>&1 &`
5. Update Helius webhook URL back to old endpoint

---

**Deployment Time**: ~15 minutes
**Difficulty**: Low (copy files, update main.py, restart)
**Risk**: Very Low (backwards compatible, graceful degradation)

Good luck! 🚀
