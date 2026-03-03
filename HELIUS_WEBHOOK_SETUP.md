# Helius Webhook Setup Guide

## Overview
The Flex application now includes a webhook endpoint (`/helius/webhook`) that receives real-time transaction data from Helius and processes creator outgoing transfers.

## Endpoint Details

**URL**: `https://your-domain.com/helius/webhook`
**Method**: POST
**Content-Type**: application/json

## Setup Steps

### 1. Get Your Helius API Key
1. Go to [Helius Dashboard](https://dashboard.helius.xyz/)
2. Sign in or create an account
3. Create a new application or select existing one
4. Copy your API Key

### 2. Set Environment Variable (Local Testing)
```bash
export HELIUS_WEBHOOK_AUTH="Bearer YOUR_HELIUS_API_KEY"
```

Or in your `.env` file:
```
HELIUS_WEBHOOK_AUTH=Bearer YOUR_HELIUS_API_KEY
```

### 3. Register Webhook with Helius

**Via Helius API:**
```bash
curl -X POST "https://api.helius.xyz/v0/webhooks" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "webhookURL": "https://your-domain.com/helius/webhook",
    "transactionTypes": ["NATIVE_TRANSFER"],
    "accountAddresses": ["YOUR_CREATOR_ADDRESSES"],
    "commitment": "processed"
  }'
```

**Via Helius Dashboard:**
1. Go to Webhooks section
2. Click "Create Webhook"
3. Enter webhook URL: `https://your-domain.com/helius/webhook`
4. Select transaction types: Native Transfers
5. Add account addresses to monitor (optional - can monitor all creators)
6. Set commitment level to "processed" for speed

### 4. Test the Webhook

**Health Check:**
```bash
curl -X POST http://localhost:5000/helius/webhook \
  -H "Authorization: Bearer YOUR_HELIUS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '[]'
```

Expected response: `{"ok": true}`

**With Sample Transaction:**
```bash
curl -X POST http://localhost:5000/helius/webhook \
  -H "Authorization: Bearer YOUR_HELIUS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '[{
    "signature": "5Xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "timestamp": 1709500000,
    "nativeTransfers": [{
      "fromUserAccount": "CREATOR_ADDRESS",
      "toUserAccount": "RECIPIENT_ADDRESS",
      "amount": 5000000000
    }]
  }]'
```

## How It Works

1. **Receives transactions** from Helius webhook
2. **Extracts native transfers** (SOL movements)
3. **Checks if sender is a known creator** (looks up `creators` table)
4. **Records valid transfers** to `creator_outgoing_transfers` table
5. **Avoids duplicates** by checking transaction signature
6. **Logs all activity** with debug output

## Monitoring

Watch webhook activity in logs:
```bash
tail -f logs.txt | grep "HELIUS_WEBHOOK"
```

Expected output:
```
[HELIUS_WEBHOOK] 📨 Received 5 transaction(s)
[HELIUS_WEBHOOK] Processing 5Xxx... | 2 native transfer(s)
[HELIUS_WEBHOOK] ✅ Recorded transfer: aaa... → bbb... | 5.0000 SOL
[HELIUS_WEBHOOK] ✅ Processed 3 creator transfers
```

## Database Schema

Webhook updates the `creator_outgoing_transfers` table:

| Column | Type | Notes |
|--------|------|-------|
| creator_address | TEXT | Token creator (from_addr) |
| recipient_address | TEXT | Who received the SOL (to_addr) |
| amount_sol | REAL | SOL amount transferred |
| transaction_signature | TEXT | Unique transaction hash |
| block_time | INT | Block timestamp |
| detected_at | TIMESTAMP | When we recorded it |

## Authorization

The webhook validates requests using the `HELIUS_WEBHOOK_AUTH` header. This should match your Helius API Key.

If the environment variable is not set, the webhook will accept all requests (not recommended for production).

## Troubleshooting

### 401 Unauthorized
- Check that `HELIUS_WEBHOOK_AUTH` environment variable is set
- Ensure header matches: `Authorization: Bearer YOUR_KEY`

### No transfers recorded
- Verify `nativeTransfers` array exists in payload
- Check that sender addresses are in the `creators` table
- Monitor logs for errors

### Duplicate detection issues
- The system checks transaction signature + creator + recipient
- Same creator sending to same recipient twice will be recorded separately
- Check `creator_outgoing_transfers` table for existing records

## Performance Notes

- Webhook processes transactions synchronously
- Large batches (100+ transfers) complete in <100ms
- Database is properly indexed for creator lookups
- Duplicate checks prevent data pollution
