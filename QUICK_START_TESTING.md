# Transaction Viewer - Quick Start Testing Guide

**Status**: ✅ Ready to Test
**Date**: 2026-01-29

## Prerequisites

- Flask server running on port 5002
- SQLite database with test data
- Browser (Chrome, Firefox, Safari, Edge)

## Quick Start (5 minutes)

### 1. Start Flask Server

```bash
python3 main.py
```

Expected output:
```
 * Serving Flask app 'main'
 * Debug mode: off
 * Running on http://127.0.0.1:5002
```

### 2. Open Browser

Navigate to: **http://localhost:5002**

You should see a table with token data.

### 3. Find the Test Creator

**Creator Address**: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh`

Look in the main table and find this creator address (it will appear as a short clickable link).

### 4. Click Creator Address

Click on the creator address link.

**Expected**: Creator Details modal opens
- Title: "Creator Details - qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh"
- Shows stats boxes (Total Tokens, Funding, Funders, Network Size, Status)
- Shows "Tokens Launched" table below

### 5. Find Test Token in Table

Look for token: **`ExampleTokenWithQNGCreatorPump`**

In the "Tokens Launched" table, find this row.

### 6. Click "View Raw" Button

In the CREATE Tx column, next to the transaction signature link, click the **"View Raw"** button.

**Expected**: Transaction Viewer modal opens
- Shows "Transaction Details" title
- Shows "🔗 View on Solscan" and "📋 Copy Signature" buttons
- Shows "Account Keys (jsonParsed)" section with formatted JSON
- Shows "Fee Payer (Creator)" section with address

### 7. Verify Fee Payer

In the "Fee Payer (Creator)" section, you should see:

```
qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh

✓ Fee payer (always first signer) = transaction creator
```

The address should have a **GREEN border** (indicating valid signer).

### 8. Test Buttons

**Test "View on Solscan"**:
- Click the button
- Should open: https://solscan.io/tx/3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC
- Opens in new browser tab

**Test "Copy Signature"**:
- Click the button
- Should show alert: "Copied to clipboard!"
- Transaction signature copied to clipboard

### 9. Test Modal Closing

Try all three ways to close the Transaction Viewer modal:

1. **Click X button** (top-right) → Modal closes ✅
2. **Click outside modal** (dark area) → Modal closes ✅
3. **Press Escape key** → Modal closes ✅

---

## What You Should See

### Main Page Table
```
┌──────────────┬──────────────┬─────────┐
│ Creator      │ Risk Level   │ ...     │
├──────────────┼──────────────┼─────────┤
│ qNGhUruCG... │ MEDIUM       │ ...     │
└──────────────┴──────────────┴─────────┘
```

Click the creator address.

### Creator Modal
```
Creator Details - qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh

📊 Stats:
[Total Tokens: 1] [Funding: — SOL] [Funders: 0] [Network: 0 wallets] [Status: ✅ Clean]

📋 Tokens Launched:
┌────────────────────────┬──────────┬───────┬────────────┬──────────────┐
│ Token Mint             │ Created  │ Risk  │ Market Cap │ CREATE Tx    │
├────────────────────────┼──────────┼───────┼────────────┼──────────────┤
│ ExampleTokenWithQN...  │ Jan 29   │ MED   │ $50.00k    │ 3v5kHrM... View Raw │
└────────────────────────┴──────────┴───────┴────────────┴──────────────┘
```

Click "View Raw" button.

### Transaction Viewer Modal
```
Transaction Details - 3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1Bb...

🔗 View on Solscan    📋 Copy Signature

Account Keys (jsonParsed)
[
  {
    "pubkey": "qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh",
    "signer": true,
    "source": "transaction",
    "writable": true
  },
  ... (26 more accounts)
]

Fee Payer (Creator)
═══════════════════════════════════════════════════════════════
qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh
✓ Fee payer (always first signer) = transaction creator
═══════════════════════════════════════════════════════════════
```

---

## Verification Checklist

After completing the quick start, verify:

- [ ] **Main page loads** - Table visible with creator addresses
- [ ] **Creator clickable** - Address styled as link and clickable
- [ ] **Creator modal opens** - Shows title with creator address
- [ ] **Test token visible** - `ExampleTokenWithQNGCreatorPump` in tokens table
- [ ] **View Raw button visible** - Cyan button next to CREATE tx signature
- [ ] **Transaction viewer opens** - Modal shows transaction details
- [ ] **Account keys displayed** - JSON formatted with 27 accounts
- [ ] **Fee payer highlighted** - `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh` shown
- [ ] **Fee payer has green border** - Indicates valid signer
- [ ] **Solscan link works** - Opens transaction in new tab
- [ ] **Copy button works** - Shows confirmation alert
- [ ] **All close methods work** - X button, click outside, Escape key
- [ ] **No console errors** - Browser console is clean

---

## Troubleshooting

### Issue: "Creator not found in table"

**Solution**:
```bash
sqlite3 pumpswap_tokens.db
SELECT COUNT(*) FROM token_analysis WHERE earliest_tx_creator = 'qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh';
```

Should return 1. If it returns 0, test data is missing. Run:
```bash
sqlite3 pumpswap_tokens.db << 'EOF'
INSERT OR IGNORE INTO token_analysis (
    mint,
    earliest_tx_creator,
    create_tx_signature,
    bonding_curve_pda,
    created_at,
    risk_level,
    market_cap_current,
    market_cap_highest
) VALUES (
    'ExampleTokenWithQNGCreatorPump',
    'qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh',
    '3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC',
    '4N15LxhLPdB3EMLNmdiJSYJAoXiW7kh66sEboLQQsmCi',
    '2026-01-29T10:00:00Z',
    'MEDIUM',
    50000.0,
    100000.0
);
EOF
```

Then reload browser (Ctrl+R or Cmd+R).

### Issue: "View Raw button not visible"

**Solution**:
1. Open browser console: F12 (Windows/Linux) or Cmd+Option+I (Mac)
2. Check for JavaScript errors
3. If errors exist, clear cache: Ctrl+Shift+Delete (Windows) or Cmd+Shift+Delete (Mac)
4. Reload page

### Issue: "Transaction not found"

**Solution**:
1. Verify transaction signature: `3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC`
2. Check if Solana RPC is responding: Open browser console and run:
   ```javascript
   fetch('/api/transaction/3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC')
     .then(r => r.json())
     .then(d => console.log(d))
   ```
3. Should show account_keys array in console

### Issue: "Fee payer not showing or showing red border"

**Solution**:
1. Verify account keys are displaying correctly
2. First object should have `"signer": true`
3. If showing red, fee payer is not marked as signer in that account
4. This would indicate an invalid transaction format

### Issue: "Modal won't close"

**Solution**:
1. Press F12 to open console
2. Run: `document.getElementById('txViewerModal').style.display = 'none'`
3. Modal should close
4. Report the issue if this doesn't work

---

## API Testing (Advanced)

### Test Transaction Endpoint

```bash
curl "http://localhost:5002/api/transaction/3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC" | jq
```

**Expected**:
```json
{
  "signature": "3v5kHrMdRcb7VSrE7...",
  "account_keys": [
    {
      "pubkey": "qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh",
      "signer": true,
      ...
    }
  ],
  "success": true
}
```

### Test Creator Details Endpoint

```bash
curl "http://localhost:5002/api/creator-details/qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh" | jq
```

**Expected**:
```json
{
  "creator_address": "qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh",
  "tokens": [
    {
      "mint": "ExampleTokenWithQNGCreatorPump",
      "create_tx_signature": "3v5kHrMdRcb7VSrE7...",
      ...
    }
  ],
  ...
}
```

---

## Success Criteria

✅ **All of the following must pass**:

1. Flask server starts without errors
2. Main page loads in browser
3. Creator address is clickable
4. Creator modal opens
5. Test token appears in tokens table
6. "View Raw" button is visible
7. Transaction viewer modal opens
8. Account keys are displayed as JSON (27 total)
9. Fee payer shows: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh`
10. Fee payer has green border
11. Solscan link opens correctly
12. Copy button shows alert
13. All close mechanisms work
14. No console errors

**If all 13 items pass**: ✅ **SYSTEM VERIFIED - PRODUCTION READY**

---

## Estimated Time

- Setup: 1 minute (start server)
- Testing: 3-4 minutes
- **Total: 5 minutes**

---

## Additional Resources

- [TRANSACTION_VIEWER_VERIFICATION.md](TRANSACTION_VIEWER_VERIFICATION.md) - Full verification report with detailed results
- [TRANSACTION_VIEWER_TEST_GUIDE.md](TRANSACTION_VIEWER_TEST_GUIDE.md) - Comprehensive testing guide
- [EXAMPLE_TRANSACTION_PARSED.md](EXAMPLE_TRANSACTION_PARSED.md) - Transaction structure details
- [CREATOR_EXTRACTION_GUARDRAIL.md](CREATOR_EXTRACTION_GUARDRAIL.md) - Creator attribution safety mechanism

---

**Date**: 2026-01-29
**Status**: ✅ Ready for testing
**Next**: Manual browser verification → Production deployment

