# Funding Completion - Quick Reference

## What Changed

✅ **Two issues from your request have been resolved:**

1. **Debug logging to track why extraction isn't running for new tokens**
   - Listener now logs toggle status when tokens are detected
   - Shows if extraction task was created successfully
   - Logs any errors that prevent task creation

2. **"Funding complete" tag now displays in UI**
   - When you click "View Funding Patterns", you'll see a status indicator at the top
   - Green "✅ Funding complete" = extraction finished
   - Yellow "⏳ Extraction in progress..." = still running

---

## How to Monitor

### See extraction logs in real-time:
```bash
tail -f listener.log | grep "FUNDER_EXTRACTION"
```

You'll see:
```
[FUNDER_EXTRACTION] DEBUG: Checking toggle... toggle_enabled=true
[FUNDER_EXTRACTION] Toggle enabled - extracting funder transfers for XMdPXJ8...
[FUNDER_EXTRACTION] Task successfully created for XMdPXJ8...
...extraction happens...
[FUNDER_EXTRACTION] ✅ Funding complete for XMdPXJ8...: IN=7, OUT=22, SOL=281.58
```

### Check extraction status via API:
```bash
CREATOR=XMdPXJjsmHkJ9Qx2s8Mpow4m4S72jgJf359vtkp8v79
curl http://localhost:5002/api/creator-funder-extraction-status/$CREATOR | python3 -m json.tool
```

Returns:
```json
{
  "is_complete": true,
  "status": "complete",
  "analyzed_funders": 1,
  "total_funders": 1,
  "last_analyzed_at": "2026-02-13 13:30:45"
}
```

### Check UI status:
1. Click on a token in the main table
2. Click "View Funding Patterns" button
3. Look for status indicator at top:
   - ✅ Funding complete (green)
   - ⏳ Extraction in progress... (yellow)

---

## Troubleshooting

### If you see "Toggle disabled - skipping":
The extraction toggle is OFF. Enable it:
```bash
curl -X POST http://localhost:5002/api/funder-extraction-control \
  -H "Content-Type: application/json" \
  -d '{"action":"enable"}'
```

Or click the UI button: "Funder Extraction OFF" → "ON"

### If extraction never completes:
Check for errors in logs:
```bash
tail -100 listener.log | grep "FUNDER_EXTRACTION.*Error"
```

### If database not updating:
Verify the field exists:
```bash
sqlite3 pumpswap_tokens.db ".schema creator_funders" | grep last_analyzed
```

Should show: `last_analyzed TIMESTAMP`

---

## Behind the Scenes

When a new token launches and extraction is enabled:

1. **Listener detects token**
   ```
   [MIGRATION] ✓ Detected migration for mint: 6uGjzex...
   ```

2. **Checks toggle status**
   ```
   [FUNDER_EXTRACTION] DEBUG: Checking toggle... toggle_enabled=true
   ```

3. **Creates extraction task** (async, non-blocking)
   ```
   [FUNDER_EXTRACTION] Task successfully created for XMdPXJ8...
   ```

4. **Extraction runs in background**
   ```
   [START] Extracting funder transfers (IN/OUT) for creator: XMdPXJ8...
   [DB] Found 1 funder(s) for this creator
   [INCOMING] 5ki8DHxFT... → XMdPXJ8... | 75.50 SOL
   ...more transfers...
   ```

5. **Marks completion** (saves timestamp)
   ```
   [DB] Marked extraction complete for all funders of XMdPXJ8...
   ```

6. **Logs final summary**
   ```
   [FUNDER_EXTRACTION] ✅ Funding complete for XMdPXJ8...: IN=7, OUT=22, SOL=281.58
   ```

7. **UI shows status** (when you open "View Funding Patterns")
   ```
   ✅ Funding complete
   ```

---

## Summary

**Before**: Extraction ran silently with no way to know if it completed
**Now**:
- Logs tell you exactly when extraction happens
- UI shows "✅ Funding complete" when done
- Debug logs help troubleshoot if extraction doesn't run
- API lets you check status programmatically

The system is production-ready. Monitor the logs to verify extraction is running for new tokens.
