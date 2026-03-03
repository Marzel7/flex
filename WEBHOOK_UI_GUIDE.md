# Webhook Monitor UI - Visual Guide

## Location in Dashboard

```
Main Dashboard (http://localhost:5002/)
    ↓
Navigation Bar
├─ Tokens (tab)
├─ Networks (button)
├─ Clusters (button)
├─ Coordinated Funders (button)
├─ Hubs (button)
├─ Creator Analysis (button)
├─ 📡 Webhook (button) ← CLICK HERE
└─ 💰 RPC Metrics (button)
```

## Page Layout

```
╔════════════════════════════════════════════════════════════════════╗
║  📡 Webhook Monitor                              [← Back]          ║
╚════════════════════════════════════════════════════════════════════╝

[🔄 Refresh Now] Auto-refreshing every 5 seconds

┌──────────────────┬──────────────────┬──────────────────┬──────────────┐
│ Webhooks         │ Transfers        │ Transfers (24h)  │ Last Activity│
│ Received         │ Processed        │                  │              │
│                  │                  │                  │              │
│       145        │      2,847       │       523        │  3 min ago   │
│ Unique Sigs      │ Total Movements  │ From 24 hours    │ 2024-01-15   │
│ ● Active         │                  │                  │ 14:22:45     │
└──────────────────┴──────────────────┴──────────────────┴──────────────┘

Recent Transfers
┌──────────┬──────────┬────────┬──────────┬──────────┐
│ Sender   │ Receiver │ Amount │ TX Hash  │ Time     │
├──────────┼──────────┼────────┼──────────┼──────────┤
│ abc1...  │ def4...  │ ◆ 5.0  │ 5Xxx...  │ 2 min    │
│ ghi7...  │ jkl0...  │ ◆ 2.5  │ 5Yyy...  │ 5 min    │
│ mno3...  │ pqr6...  │ ◆ 1.0  │ 5Zzz...  │ 8 min    │
│          │          │        │          │          │
│ ...      │ ...      │ ...    │ ...      │ ...      │
└──────────┴──────────┴────────┴──────────┴──────────┘
```

## Color Scheme

### Metric Cards
```
┌─ Purple border (#a78bfa)
│
├─ Label: Purple (#a78bfa) - uppercase
├─ Value: Cyan (#06b6d4) - large number
└─ Badge: Green (#22c55e) if active, Gray if idle
```

### Transfers Table
```
Header:    Purple background with purple text
Rows:      Light on hover
Amount:    Green with diamond symbol (◆)
Address:   Purple monospace
Time:      Gray and small
```

## Interaction Points

### 1. Back Button
```
Top-right corner
┌─────────────────────────┐
│ 📡 Webhook Monitor  [← Back] │
└─────────────────────────┘
```
- Returns to main dashboard

### 2. Refresh Button
```
Below header
[🔄 Refresh Now]  Auto-refreshing every 5 seconds
```
- Manually update all metrics immediately
- Auto-refresh runs every 5 seconds anyway

### 3. Metric Cards (Hover Effect)
```
Normal State:      Hover State:
┌──────────────┐   ┌──────────────┐
│ Webhooks     │   │ Webhooks     │ ← brighter
│ Received     │   │ Received     │ ← border glows
│              │   │              │
│     145      │   │     145      │
└──────────────┘   └──────────────┘
```

### 4. Transfer Rows (Hover Effect)
```
Normal:     ▯ Row with text
Hover:      ▰ Row with light purple background highlight
```

## Reading the Metrics

### Webhooks Received: 145
- Helius has successfully sent 145 transactions to `/helius/webhook`
- Each unique transaction signature = 1 webhook received
- ✅ Active badge = at least 1 received
- ⚪ Idle badge = 0 received (no activity yet)

### Transfers Processed: 2,847
- 2,847 SOL transfers have been extracted from those 145 webhooks
- Some webhooks may contain multiple transfers
- This is cumulative over all time
- Grows as more webhooks are received

### Transfers (24h): 523
- 523 transfers happened in the last 24 hours
- Good indicator of current activity level
- If this is 0: webhook is not receiving current transactions

### Last Activity: 3 min ago
- Most recent webhook arrived 3 minutes ago
- If old (hours): webhook may be offline
- Absolute timestamp below for verification

## Transfer Table Details

### Sender: `abc1...`
- First 8 characters of sender address
- Shortened for readability
- Full address available in logs/database

### Receiver: `def4...`
- First 8 characters of receiver address
- Who received the SOL

### Amount: `◆ 5.0`
- Green text with diamond symbol
- Converted from lamports to SOL
- 5.0 = 5 billion lamports

### TX Hash: `5Xxx...`
- First 8 characters of transaction signature
- Full hash in logs/database
- Can copy full sig from server logs

### Time: `2 min`
- Relative time (how long ago)
- Updated live as you watch
- Shows approximate recency

## Real-Time Behavior

### Initial Load
```
[Metrics loading...]  [🔄 Refresh Now]
[Transfer rows with spinner animation]
```
- Takes ~100-200ms to load
- Shows spinner while fetching

### After Data Loads
```
[Metrics populated]   [🔄 Refresh Now]
[Transfer rows with data]
↓ (auto-refreshes in 5 seconds)
↓ (metrics update, timestamps change)
↓ (new transfers may appear)
```

### Continuous Updates
- Page auto-refreshes every 5 seconds
- Timestamps change (2 min ago → 2:05 min ago)
- New transfers added to top of table
- Old transfers push down and eventually disappear

## Status Indicators

### Active (Green Badge)
```
● Active
```
- Shows when `total_signatures > 0`
- Means webhook has received transactions
- Indicates system is working

### Idle (Gray Badge)
```
⚪ Idle
```
- Shows when `total_signatures == 0`
- Means webhook is not yet receiving data
- May be normal on first startup

## Performance

- **Page load**: ~200ms
- **Auto-refresh**: ~100ms per poll (every 5 sec)
- **Memory**: <10MB
- **CPU**: Minimal (just polling)

## Troubleshooting

### Metrics All Show 0?
1. Webhook may not be receiving data yet
2. Check Helius dashboard for webhook status
3. Verify webhook URL is correct

### Data Stuck (not updating)?
1. Click [🔄 Refresh Now] to force update
2. Check browser console (F12) for JS errors
3. Verify `/api/webhook-status` endpoint is accessible

### No Recent Transfers?
1. Webhooks may have old data
2. Current transfers may be stored elsewhere
3. Check database: `sqlite3 ... SELECT COUNT(*) FROM creator_outgoing_transfers`

### Page Won't Load?
1. Verify Flask app is running on port 5002
2. Check browser network tab (F12) for errors
3. See Flask logs for backend errors

## Mobile Experience

Page is fully responsive:
- On phones: metrics stack vertically
- On tablets: 2-column grid
- On desktop: full 4-column grid
- Table scrolls horizontally if needed

## Keyboard Shortcuts

None built-in, but works with:
- **Tab**: Navigate between elements
- **Enter**: Click buttons with focus
- **F5**: Browser refresh (reloads whole page)
- **F12**: Open developer console

## Accessibility

- Semantic HTML structure
- Good color contrast
- Monospace font for data readability
- Descriptive labels for all metrics
- Status indicators explained in text

## Technical Details

**Powered by:**
- Flask backend: `/api/webhook-status`
- JavaScript fetch API: Polls every 5 seconds
- SQLite: Queries `webhook_seen_signatures` and `creator_outgoing_transfers`
- No external dependencies (vanilla JS)

**Browser Support:**
- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Any modern browser with ES6 support
