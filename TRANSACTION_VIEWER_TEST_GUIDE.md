# Transaction Viewer - Quick Test Guide

**Date**: 2026-01-29
**Status**: Ready for Testing

---

## Test Data Added

A test token with the example transaction has been added to the database:

**Token**: `ExampleTokenWithQNGCreatorPump`
**Creator**: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh` ← Fee payer
**CREATE TX**: `3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC`

This is the exact transaction shown in the documentation.

---

## How to Test

### 1. Start Flask Server
```bash
python3 main.py
```

Navigate to: `http://localhost:5002`

### 2. Find Creator in Main Table

Look for creator: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh`
- Should appear as a short address in the main token table
- Click on the creator address link

### 3. Open Creator Modal

When you click the creator address:
- Creator Details modal opens
- Shows all tokens launched by this creator
- Shows "Tokens Launched" section with table

### 4. Find CREATE TX

In the "Tokens Launched" table:
- Look for row with token: `ExampleTokenWithQNGCreatorPump`
- In the "CREATE Tx" column, you should see:
  - Short signature (first 16 chars): `3v5kHrMdRcb7VSrE7...`
  - Solscan link (blue link text)
  - **"View Raw" button** (cyan button next to the link)

### 5. Click "View Raw" Button

When you click the "View Raw" button:
- Transaction Viewer modal opens
- Shows "Transaction Details - 3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx..."
- Shows two action buttons:
  - 🔗 "View on Solscan" (blue link)
  - 📋 "Copy Signature" (button)

### 6. Inspect Account Keys

In the Transaction Viewer modal:
- **"Account Keys (jsonParsed)"** section shows formatted JSON
- Should display 27 account objects with structure:
  ```json
  {
    "pubkey": "address",
    "signer": true/false,
    "source": "transaction",
    "writable": true/false
  }
  ```

### 7. Check Fee Payer Highlight

In the **"Fee Payer (Creator)"** section:
- Should show: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh` (green border)
- ✓ Fee payer (always first signer at accountKeys[0]) = transaction creator
- This is the creator of the token!

### 8. Verify Actions

Try the action buttons:
- **"View on Solscan"**: Opens https://solscan.io/tx/3v5kHrMdRcb7VSrE7... (blue link)
- **"Copy Signature"**: Copies signature to clipboard, shows confirmation alert

### 9. Close Modal

Test all close mechanisms:
- Click "X" button: Modal closes
- Click outside modal (dark area): Modal closes
- Press Escape key: Modal closes

---

## Expected Results

✅ **Test Pass Conditions**:
1. Creator found in main table
2. Creator modal opens when clicked
3. Test token appears in "Tokens Launched" table
4. CREATE tx signature displayed with link and button
5. "View Raw" button opens transaction viewer
6. Account keys JSON displayed correctly
7. Fee payer shown: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh`
8. Fee payer has green border (valid signer)
9. Solscan link works
10. Copy signature works
11. All close mechanisms work

---

## Troubleshooting

### Issue: "Transaction not found"
- Check browser console for RPC errors
- Verify transaction signature is correct
- Try viewing on Solscan first to confirm it exists

### Issue: Fee payer not showing
- Check that accountKeys are displayed as JSON
- Should be in first object: `accountKeys[0].pubkey`
- If showing red border: Fee payer is not marked as signer

### Issue: "View Raw" button not visible
- Check browser console for JavaScript errors
- Verify CREATE tx signature exists in database
- Reload page to clear cache

### Issue: Modal won't close
- Try pressing Escape key
- Try clicking X button
- Check browser console for JavaScript errors

---

## Database Setup

The test token is stored as:
```sql
INSERT INTO token_analysis (
    mint = 'ExampleTokenWithQNGCreatorPump',
    earliest_tx_creator = 'qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh',
    create_tx_signature = '3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC',
    bonding_curve_pda = '4N15LxhLPdB3EMLNmdiJSYJAoXiW7kh66sEboLQQsmCi'
);
```

---

## Real Transaction Info

For reference, here's what the transaction contains:

**Fee Payer (Creator)**: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh`

**Account Keys**: 27 total
- [0]: Fee payer (signer=true, writable=true) ← THE CREATOR
- [1-11]: User accounts (signer=false, writable=true)
- [12-26]: System programs (signer=false, writable=false)

**Programs Involved**:
- Token Program: `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`
- System Program: `11111111111111111111111111111111`
- Pump.fun AMM: `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`
- And others...

---

## Next Steps After Testing

1. ✅ Verify all UI elements work
2. ✅ Confirm fee payer is correctly identified
3. ✅ Test with other real transactions
4. ✅ Verify Solscan links work
5. ✅ Check copy-to-clipboard functionality
6. Ready for production testing with real tokens

---

**Verification Checklist**:
- [ ] Transaction viewer modal opens
- [ ] Account keys displayed as JSON
- [ ] Fee payer correctly identified
- [ ] "View on Solscan" link works
- [ ] "Copy Signature" button works
- [ ] Modal closes properly
- [ ] No console errors

**Status**: Ready for testing
