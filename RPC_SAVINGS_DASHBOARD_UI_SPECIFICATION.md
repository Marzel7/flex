# RPC Savings Dashboard - UI/UX Specification

**Date**: March 6, 2026
**Purpose**: Visualize RPC credit usage, optimization ROI, and tracking coverage
**Audience**: Engineering & Operations teams

---

## Table of Contents

1. [Overview](#overview)
2. [KPI Row](#kpi-row)
3. [Savings by Optimization Layer](#savings-by-optimization-layer)
4. [Savings by Component](#savings-by-component)
5. [Daily Trend Chart](#daily-trend-chart)
6. [Cache Efficiency Panel](#cache-efficiency-panel)
7. [Top Expensive Methods](#top-expensive-methods)
8. [Tracked vs Untracked](#tracked-vs-untracked)
9. [Page Layout](#page-layout)
10. [Color Semantics](#color-semantics)
11. [Responsive Design](#responsive-design)

---

## Overview

The dashboard answers these key questions:

1. **How much are we spending?** Actual RPC credits consumed
2. **How much are we saving?** Credits avoided through optimizations
3. **What's the ROI?** Savings as % of potential spend
4. **Which optimizations work?** Breakdown by layer and component
5. **Are we tracking everything?** Coverage vs Helius billed usage
6. **What should we optimize next?** Expensive methods and gaps

---

## KPI Row

**Layout**: Horizontal row of 6 cards spanning full width
**Position**: Top of page (above all sections)
**Update frequency**: Every 5 seconds (real-time)
**Time window**: Last 24 hours (configurable)

### Card 1: Actual RPC Credits
- **Label**: "Actual RPC Credits"
- **Primary value**: Large number (e.g., `78,000`)
- **Unit**: "credits"
- **Secondary text**: "Last 24h"
- **Color**: Blue (#3b82f6)
- **Icon**: Arrow up (indicating ongoing spend)
- **Trend**: Optional mini sparkline showing 24h history

### Card 2: Credits Saved
- **Label**: "Credits Saved"
- **Primary value**: Large number (e.g., `34,000`)
- **Unit**: "credits"
- **Secondary text**: "Last 24h"
- **Color**: Green (#22c55e)
- **Icon**: Checkmark (indicating optimization success)
- **Trend**: Optional mini sparkline showing 24h history

### Card 3: Estimated Without Optimizations
- **Label**: "Est. Without Optimizations"
- **Primary value**: Large number (e.g., `112,000`)
- **Unit**: "credits"
- **Secondary text**: "actual + saved"
- **Color**: Amber (#fbbf24)
- **Icon**: Gauge (indicating "what if" metric)
- **Calculation**: `actual_credits + saved_credits`

### Card 4: Savings %
- **Label**: "Savings %"
- **Primary value**: Percentage (e.g., `30.4%`)
- **Unit**: None (already percentage)
- **Secondary text**: "of potential spend"
- **Color**: Green (#22c55e)
- **Icon**: Percent symbol or trending up
- **Calculation**: `(saved_credits / (actual_credits + saved_credits)) * 100`
- **Styling**: Larger font if savings > 25%

### Card 5: Tracked Calls
- **Label**: "Tracked Calls"
- **Primary value**: Large number (e.g., `12,482`)
- **Unit**: "calls"
- **Secondary text**: "Last 24h"
- **Color**: Blue (#3b82f6)
- **Icon**: Database icon
- **Note**: Counts actual RPC calls in `rpc_metrics` table

### Card 6: Tracking Coverage %
- **Label**: "Tracking Coverage"
- **Primary value**: Percentage (e.g., `94.8%`)
- **Unit**: None (already percentage)
- **Secondary text**: "vs Helius billed"
- **Color**: Green if > 90%, Amber if 50-90%, Red if < 50%
- **Icon**: Target or checkmark
- **Calculation**: `tracked_calls / helius_billed_credits * 100`
- **Warning**: Red background if < 50% (instrumentation gap)

---

## Savings by Optimization Layer

**Section Type**: Table (primary) or Horizontal Bar Chart (alternative)
**Position**: Top left, below KPI row
**Size**: 50% width on desktop, full width on tablet/mobile
**Rows**: 5-8 optimization layers
**Update frequency**: Every 60 seconds
**Sorting**: By credits saved (descending)

### Columns

| Column | Type | Format | Purpose |
|--------|------|--------|---------|
| Optimization Layer | Text | Plain (tx_cache, wallet_cache, etc.) | Identifies the optimization |
| Events | Number | Integer (e.g., `390`) | How many times it triggered |
| Credits Saved | Number | Formatted (e.g., `12,450`) | Total credits avoided |
| % of Total | Percentage | Formatted (e.g., `36.6%`) | Proportion of all savings |
| Avg per Event | Number | Decimal (e.g., `31.9`) | `credits_saved / events` |

### Example Data

```
Optimization Layer      | Events | Credits Saved | % of Total | Avg/Event
------------------------+--------+---------------+------------+----------
tx_cache               |   390  |    12,450     |   36.6%    |   31.9
webhook_filter         |   220  |     6,200     |   18.2%    |   28.2
wallet_cache           |   410  |     8,300     |   24.4%    |   20.2
fingerprint_cluster    |   140  |     3,900     |   11.5%    |   27.9
funder_skip            |   115  |     3,150     |    9.3%    |   27.4
```

### Visual Styling

- **Header**: Bold, light gray background
- **Rows**: Alternating white/light gray for readability
- **Hover**: Highlight row with light blue background
- **Colors**:
  - Layer name: Dark gray
  - Events: Blue text
  - Credits Saved: Green text (positive)
  - % of Total: Amber text
  - Avg per Event: Blue text

### Interactions

- **Click on row**: Show detailed breakdown by section for that layer
- **Sort by column**: Enable ascending/descending sort
- **Time picker**: Allow 24h / 7d / 30d views

---

## Savings by Component

**Section Type**: Table
**Position**: Top right, below KPI row (next to "Savings by Optimization Layer")
**Size**: 50% width on desktop, full width on tablet/mobile
**Rows**: 4-6 system components (sections)
**Update frequency**: Every 60 seconds
**Sorting**: By savings % (descending)

### Columns

| Column | Type | Format | Purpose |
|--------|------|--------|---------|
| Section | Text | Plain (listener, creator_funding, etc.) | System component |
| Actual Credits | Number | Formatted (e.g., `18,400`) | RPC credits used |
| Credits Saved | Number | Formatted (e.g., `6,100`) | Credits avoided |
| Est. Without Opts | Number | Formatted (e.g., `24,500`) | `actual + saved` |
| Savings % | Percentage | Formatted (e.g., `24.9%`) | `saved / estimated * 100` |

### Example Data

```
Section              | Actual Credits | Saved Credits | Est. Without Opts | Savings %
---------------------+----------------+---------------+-------------------+-----------
listener             |     18,400     |     6,100     |     24,500        |  24.9%
creator_funding      |     22,300     |    14,200     |     36,500        |  38.9%
funder_incoming      |     19,100     |     9,800     |     28,900        |  33.9%
clustering           |      4,200     |     1,600     |      5,800        |  27.6%
```

### Visual Styling

- **Header**: Bold, light gray background
- **Rows**: Alternating white/light gray
- **Hover**: Highlight with light blue
- **Colors**:
  - Section: Dark gray
  - Actual Credits: Blue text
  - Credits Saved: Green text (positive)
  - Est. Without Opts: Amber text
  - Savings %: Green text (positive metric)

### Interactions

- **Click on section row**: Drill down to show optimization layers used by that section
- **Sort by column**: Enable any column sort
- **Time picker**: Shared with Optimization Layer section (24h / 7d / 30d)

---

## Daily Trend Chart

**Section Type**: Line chart (3 series)
**Position**: Full width, below both tables
**Height**: 300px (desktop), 250px (tablet), 200px (mobile)
**Update frequency**: Every 60 seconds
**Time range**: 24h (default), with toggle for 7d / 30d
**X-axis**: Time (hour of day, or day of week/month)
**Y-axis**: Credits

### Series

1. **Actual Credits** (Blue line, #3b82f6)
   - Definition: Daily sum of all RPC credits used
   - Data source: `SUM(credits)` from `rpc_metrics` per day
   - Width: 2px, solid

2. **Credits Saved** (Green line, #22c55e)
   - Definition: Daily sum of all credits_saved where credits_saved > 0
   - Data source: `SUM(credits_saved)` from `rpc_metrics` per day
   - Width: 2px, solid

3. **Estimated Without Optimizations** (Amber line, #fbbf24)
   - Definition: Actual + Saved (shows potential spend)
   - Data source: Calculated per day
   - Width: 2px, dashed (to distinguish from actual)

### Visual Elements

- **Grid lines**: Light gray, subtle
- **Hover tooltip**: Shows all 3 values for hovered day
- **Legend**: Bottom of chart, clickable to toggle series visibility
- **Annotations**: Optional - mark when major optimizations were deployed

### Insights

This chart reveals:
- Whether savings are increasing or decreasing
- Whether a new optimization reduced burn rate
- Whether a regression removed savings
- Daily volatility and trends

---

## Cache Efficiency Panel

**Section Type**: 2x3 grid of small cards
**Position**: Left side, below Daily Trend Chart
**Size**: 40% width on desktop, full width on tablet/mobile
**Update frequency**: Every 60 seconds

### Card 1: Cache Skip Count
- **Label**: "Cache Skips"
- **Value**: Large number (e.g., `1,284`)
- **Unit**: "calls"
- **Color**: Blue (#3b82f6)
- **Definition**: Count of RPC calls where cache_action='skip'

### Card 2: Cache Refresh Count
- **Label**: "Cache Refreshes"
- **Value**: Large number (e.g., `318`)
- **Unit**: "calls"
- **Color**: Blue (#3b82f6)
- **Definition**: Count of RPC calls where cache_action='refresh'

### Card 3: Full Scan Count
- **Label**: "Full Scans"
- **Value**: Large number (e.g., `74`)
- **Unit**: "calls"
- **Color**: Amber (#fbbf24)
- **Definition**: Count of RPC calls where cache_action='full_scan'
- **Note**: Full scans are necessary but less efficient

### Card 4: Credits Saved by Cache
- **Label**: "Cache Credits Saved"
- **Value**: Large number (e.g., `18,240`)
- **Unit**: "credits"
- **Color**: Green (#22c55e)
- **Definition**: Sum of credits_saved where cache_action in ('skip', 'refresh')

### Card 5: Avg Saved per Skip
- **Label**: "Avg per Skip"
- **Value**: Decimal (e.g., `14.2`)
- **Unit**: "credits"
- **Color**: Green (#22c55e)
- **Calculation**: `credits_saved / skip_count`

### Card 6: Cache Efficiency %
- **Label**: "Cache Efficiency"
- **Value**: Percentage (e.g., `94.5%`)
- **Unit**: None
- **Color**: Green if > 80%, Amber if 50-80%, Red if < 50%
- **Calculation**: `(skip_count + refresh_count) / (skip_count + refresh_count + full_scan_count) * 100`
- **Definition**: Percentage of cache attempts that succeeded (skip/refresh vs full scan)

---

## Top Expensive Methods

**Section Type**: Table
**Position**: Right side, below Daily Trend Chart (next to Cache Efficiency)
**Size**: 60% width on desktop, full width on tablet/mobile
**Rows**: Top 8-10 methods by actual credits
**Update frequency**: Every 60 seconds
**Sorting**: By Actual Credits (descending)

### Columns

| Column | Type | Format | Purpose |
|--------|------|--------|---------|
| Method | Text | Plain (getTransaction, getSignaturesForAddress, etc.) | RPC method name |
| Actual Credits | Number | Formatted (e.g., `28,000`) | Total credits used |
| Calls | Number | Integer (e.g., `2,800`) | Number of times called |
| Possible Saved | Number | Formatted (e.g., `11,000`) | Potential savings if optimized |
| Opportunity | Badge | Low / Medium / High | Optimization priority |

### Example Data

```
Method                                 | Actual Cr. | Calls | Possible Saved | Opportunity
----------------------------------------+------------+-------+----------------+------------
getTransaction                         |   28,000   | 2,800 |    11,000      |   High
getSignaturesForAddress                |   21,000   | 2,100 |     8,400      |   High
helius_enhanced_transactions_batch     |   15,000   |  150  |     2,000      |   Medium
getProgramAccounts                     |    6,000   |  600  |     1,100      |   Medium
logsSubscribe                          |    4,200   |  140  |       800      |   Low
getAccountInfo                         |    2,100   | 2,100 |       200      |   Low
```

### Opportunity Scoring

- **High**: Possible Saved > 30% of Actual Credits
- **Medium**: Possible Saved 10-30% of Actual Credits
- **Low**: Possible Saved < 10% of Actual Credits

### Visual Styling

- **Header**: Bold, light gray background
- **Rows**: Alternating white/light gray
- **Hover**: Highlight row with light blue
- **Colors**:
  - Method: Dark gray
  - Actual Credits: Blue text
  - Calls: Gray text
  - Possible Saved: Green text
  - Opportunity (High): Red badge, (Medium): Amber badge, (Low): Gray badge

### Interactions

- **Click on method**: Show detailed breakdown by section and optimization_layer
- **Sort by column**: Enable any column sort
- **Opportunity badge**: Clickable to filter by opportunity level

---

## Tracked vs Untracked

**Section Type**: Comparison cards + gauge chart
**Position**: Full width, bottom of page
**Update frequency**: Every 60 seconds
**Purpose**: Build trust in data accuracy and identify instrumentation gaps

### Layout

Left side: 4 KPI cards (stacked vertically)
Right side: Gauge chart (optional, visual representation)

### Card 1: Helius Billed Credits
- **Label**: "Helius Billed"
- **Value**: Large number (e.g., `82,450`)
- **Unit**: "credits"
- **Color**: Amber (#fbbf24)
- **Source**: "Helius account API"
- **Note**: This is the source of truth for billing

### Card 2: Locally Tracked Credits
- **Label**: "Tracked Locally"
- **Value**: Large number (e.g., `78,000`)
- **Unit**: "credits"
- **Color**: Blue (#3b82f6)
- **Source**: "`rpc_metrics` table"
- **Note**: Our instrumentation

### Card 3: Untracked Credits
- **Label**: "Untracked Usage"
- **Value**: Large number (e.g., `4,450`)
- **Unit**: "credits"
- **Color**: Red (#ef4444)
- **Calculation**: `helius_billed - tracked_locally`
- **Note**: Credits we were charged for but didn't track

### Card 4: Tracking Coverage %
- **Label**: "Coverage"
- **Value**: Percentage (e.g., `94.6%`)
- **Unit**: None
- **Color**: Green if > 90%, Amber if 50-90%, Red if < 50%
- **Calculation**: `tracked_locally / helius_billed * 100`
- **Status**: Shows confidence in data accuracy
- **Warning**: If < 80%, recommend adding more instrumentation

### Gauge Chart (Optional Right Side)

- **Type**: Circular progress gauge
- **Filled**: Blue (tracked)
- **Unfilled**: Red (untracked)
- **Center**: Percentage label (e.g., `94.6%`)
- **Interpretation**: Visual representation of tracking completeness

### Context Panel

Below cards: Optional explanation text

```
"Helius billed credits show what we were charged for. Locally tracked credits
are what we instrumented in the codebase. The gap (Untracked Usage) represents
RPC calls we didn't track (non-instrumented processes, failed requests, WebSocket
subscriptions, or internal retries). If coverage is below 80%, consider:
- Adding instrumentation to background processes
- Checking for WebSocket usage not in rpc_metrics
- Verifying error handling doesn't hide calls"
```

---

## Page Layout

### Desktop Layout (1440px+)

```
┌─────────────────────────────────────────────────────────────┐
│                         KPI ROW (6 cards)                   │
│ ┌──────────┬──────────┬──────────┬──────────┬──────────┬───┐ │
│ │ Actual   │ Saved    │ Estimated│ Savings %│ Tracked  │Cov│ │
│ │ Credits  │ Credits  │ Without  │          │ Calls    │% │ │
│ └──────────┴──────────┴──────────┴──────────┴──────────┴───┘ │
└─────────────────────────────────────────────────────────────┘

┌───────────────────────────┐ ┌───────────────────────────┐
│  Savings by               │ │  Savings by               │
│  Optimization Layer       │ │  Component                │
│  (Table)                  │ │  (Table)                  │
│                           │ │                           │
│  tx_cache      | 36.6%    │ │  listener      | 24.9%    │
│  webhook_filter| 18.2%    │ │  creator_fund. | 38.9%    │
│  wallet_cache  | 24.4%    │ │  funder_incom. | 33.9%    │
│  etc.          |          │ │  clustering    | 27.6%    │
└───────────────────────────┘ └───────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│            Daily Trend Chart (3 series)                      │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 120K │                     Amber (Est. Without)         ││
│  │      │    ╱╲                                    ╱╲       ││
│  │ 100K │   ╱  ╲    Blue (Actual)                ╱  ╲      ││
│  │      │  ╱    ╲  ╱╲                         ╱╲╱    ╲     ││
│  │  80K │ ╱      ╲╱  ╲                     ╱╲╱        ╲    ││
│  │      │        Green (Saved)            ╱  ╲                  ││
│  │  60K │                             ╱╲╱      ╲                ││
│  │      │                         ╱╲╱          ╲               ││
│  │  40K │                     ╱╲╱                              ││
│  │      │                ╱╲╱                                   ││
│  │  20K │            ╱╱                                        ││
│  │      │_________________________________________________________││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘

┌──────────────────────────┐ ┌──────────────────────────────┐
│  Cache Efficiency Panel  │ │  Top Expensive Methods       │
│  (6 cards, 2x3 grid)     │ │  (Table)                     │
│                          │ │                              │
│ ┌────┬────┬────┐        │ │ getTransaction      28,000   │
│ │Skips│Refresh│Scans│    │ │ getSignatures     21,000   │
│ │1,284│  318  │ 74  │    │ │ helius_enhanced   15,000   │
│ └────┴────┴────┘        │ │ getProgramAccounts 6,000   │
│ ┌────┬────┬────┐        │ │ logsSubscribe      4,200   │
│ │Saved│Avg  │Eff.│    │ │ getAccountInfo     2,100   │
│ │18,240│14.2│94.5%│    │ │                              │
│ └────┴────┴────┘        │ │                              │
└──────────────────────────┘ └──────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│         Tracked vs Untracked (4 cards + optional gauge)      │
│ ┌──────────┬──────────┬──────────┬──────────┐              │
│ │ Helius   │ Tracked  │ Untracked│ Coverage │   Gauge:     │
│ │ Billed   │ Locally  │ Usage    │ %        │   ◐ 94.6%    │
│ │ 82,450   │ 78,000   │ 4,450    │ 94.6%    │              │
│ │ credits  │ credits  │ credits  │          │              │
│ └──────────┴──────────┴──────────┴──────────┘              │
│                                                              │
│ Context: Helius billed shows what you paid. Tracked shows...│
└─────────────────────────────────────────────────────────────┘
```

### Tablet Layout (768px - 1439px)

```
Stack sections vertically:
- KPI Row (scrollable horizontally if needed)
- Savings by Layer (full width)
- Savings by Component (full width)
- Daily Trend (full width)
- Cache Efficiency (full width)
- Top Methods (full width)
- Tracked vs Untracked (full width)
```

### Mobile Layout (< 768px)

```
Stack all sections vertically:
- KPI Row (cards may scroll horizontally)
- Savings by Layer (scrollable table)
- Savings by Component (scrollable table)
- Daily Trend (full width, reduced height)
- Cache cards (vertical stack, 1 per row)
- Top Methods (scrollable table)
- Tracking cards (vertical stack)
```

---

## Color Semantics

### Primary Colors

| Color | Hex | Usage | Semantic |
|-------|-----|-------|----------|
| Blue | #3b82f6 | Actual usage, calls, data | Neutral/informational |
| Green | #22c55e | Savings, optimization success | Positive |
| Amber | #fbbf24 | Estimated/potential, requires attention | Warning |
| Red | #ef4444 | Untracked, gaps, problems | Critical/action needed |
| Gray | #6b7280 | Neutral text, secondary info | Neutral |

### Usage Rules

- **Blue**: Actual RPC credits, tracked calls, observed data (what we know)
- **Green**: Savings, avoided credits, optimization success (good outcome)
- **Amber**: Estimated values, "what if" scenarios, warnings (be aware)
- **Red**: Untracked credits, coverage < 50%, gaps (action needed)
- **Gray**: Labels, secondary text, neutral info

### Accessibility

- Ensure sufficient contrast ratio (WCAG AA minimum 4.5:1)
- Don't rely on color alone (use icons, text, badges)
- Provide patterns (solid, dashed, dotted) for colorblind users
- Use icons alongside colors for meaning

---

## Responsive Design

### Breakpoints

```
Mobile:  < 768px
Tablet:  768px - 1439px
Desktop: >= 1440px
```

### Behavior

| Component | Desktop | Tablet | Mobile |
|-----------|---------|--------|--------|
| KPI Row | 6 cards/row | 3 cards/row | 2 cards/row (scroll) |
| Tables | Side-by-side | Full width, stacked | Scrollable |
| Charts | Full width | Full width | Reduced height, touch-friendly |
| Gauges | Visible | Visible | Hidden (show card only) |

### Touch Interactions

- **Swipe left/right**: Move through time periods (if applicable)
- **Tap row**: Show detail modal instead of drill-down navigation
- **Long press**: Show tooltip with explanation
- **Pinch zoom**: Unavailable (fixed to viewport)

---

## Refresh & Real-Time Updates

### Update Frequencies

| Component | Frequency | Rationale |
|-----------|-----------|-----------|
| KPI Row | 5 seconds | Should reflect near-real-time spend |
| Tables | 60 seconds | Updated less frequently, prevents jitter |
| Charts | 60 seconds | Daily aggregation, updates once per minute |
| Tracking section | 60 seconds | Account-level metrics, slower changes |

### Loading States

- Show skeleton loaders while fetching data
- Disable sorting/filtering during load
- Display last-update timestamp (e.g., "Updated 30s ago")
- Use pulsing animation for updates

### Error Handling

- Show error banner at top if data fetch fails
- Display "Last successful update: XX minutes ago"
- Provide "Retry" button
- Allow viewing cached/stale data with warning

---

## Interactions & Controls

### Time Window Selector

Location: Top right, near KPI row
Options:
- Last 24 hours (default)
- Last 7 days
- Last 30 days
- Custom date range (optional)

Behavior:
- Updates KPI values
- Updates tables and charts
- Maintains scroll position

### Table Sorting

- Click column header to sort
- Show sort indicator (▲/▼)
- Default sort: By "value" column (descending)
- Persist sort preference in browser localStorage

### Drill-Down Navigation

**From Savings by Layer table:**
- Click row → Show Savings by Component breakdown for that optimization layer
- Breadcrumb: "Optimizations > tx_cache"

**From Top Methods table:**
- Click row → Show which optimization_layers catch this method
- Show historical trend for that method

### Time Range Picker

- Date picker dropdown or calendar widget
- Presets: Last 24h, 7d, 30d, This Month, Last Month
- Custom range button for arbitrary dates
- Apply & Cancel buttons

---

## API Data Shape

The backend should provide an endpoint like:

```json
GET /api/rpc/dashboard?window=24h

{
  "timestamp": "2026-03-06T14:30:00Z",
  "window_hours": 24,
  "summary": {
    "actual_credits": 78000,
    "saved_credits": 34000,
    "estimated_without_optimizations": 112000,
    "savings_pct": 30.36,
    "tracked_calls": 12482,
    "tracking_coverage_pct": 94.76
  },
  "by_optimization_layer": [
    {
      "optimization_layer": "tx_cache",
      "events": 390,
      "saved_credits": 12450,
      "savings_pct": 36.61,
      "avg_per_event": 31.92
    },
    ...
  ],
  "by_section": [
    {
      "section": "listener",
      "actual_credits": 18400,
      "saved_credits": 6100,
      "estimated_without_optimizations": 24500,
      "savings_pct": 24.90
    },
    ...
  ],
  "trend": [
    {
      "date": "2026-03-05",
      "actual_credits": 77800,
      "saved_credits": 33900,
      "estimated": 111700
    },
    ...
  ],
  "cache_stats": {
    "cache_skip_count": 1284,
    "cache_refresh_count": 318,
    "cache_full_scan_count": 74,
    "credits_saved_by_cache": 18240,
    "avg_saved_per_skip": 14.21,
    "cache_efficiency_pct": 94.47
  },
  "top_methods": [
    {
      "method": "getTransaction",
      "actual_credits": 28000,
      "calls": 2800,
      "possible_saved_credits": 11000,
      "opportunity": "High"
    },
    ...
  ],
  "tracking": {
    "helius_billed_credits": 82450,
    "tracked_locally_credits": 78000,
    "untracked_credits": 4450,
    "coverage_pct": 94.58
  }
}
```

---

## Accessibility & Inclusive Design

### Keyboard Navigation

- Tab through KPI cards and tables
- Enter/Space to expand rows
- Arrow keys to navigate tables
- Esc to close modals

### Screen Reader Support

- Descriptive `aria-label` attributes on all cards
- Table `<caption>` elements describing purpose
- Use semantic HTML (`<table>`, `<caption>`, `<thead>`, `<tbody>`)
- Provide text alternatives for charts (data table option)
- Status updates announced via `aria-live` regions

### Color Contrast

- Ensure all text on colored backgrounds meets WCAG AA (4.5:1)
- Provide patterns/icons alongside color-coded information
- Test with colorblind simulator

### Typography

- Minimum font size: 14px
- Line height: 1.5
- Maximum line length: 80 characters
- Use sans-serif fonts (Helvetica, Inter, Segoe UI)

---

## Future Enhancements

### Phase 2

- [ ] Export dashboard as PDF
- [ ] Schedule email reports
- [ ] Comparison mode (week-over-week, month-over-month)
- [ ] Anomaly detection and alerts
- [ ] Custom dashboard builder

### Phase 3

- [ ] Machine learning to predict optimization opportunities
- [ ] Recommendation engine ("Consider enabling wallet_cache for creator_funding")
- [ ] Cost calculator ("If you deploy X optimization, save $Y/month")
- [ ] Integration with billing system

### Phase 4

- [ ] Mobile app with push notifications
- [ ] Slack integration (daily summary, anomaly alerts)
- [ ] API for programmatic access
- [ ] GraphQL endpoint for dashboard queries

---

## Implementation Checklist

- [ ] Design mockups in Figma (KPI row, tables, charts)
- [ ] Implement React components for each section
- [ ] Create API endpoints in `rpc_metrics_api.py`
- [ ] Add backend calculations for derived metrics
- [ ] Implement responsive layouts
- [ ] Add time window selector
- [ ] Test accessibility (keyboard, screen reader, color contrast)
- [ ] Add loading states and error handling
- [ ] Implement real-time updates (WebSocket or polling)
- [ ] Add unit tests for calculations
- [ ] Document component props and API contract
- [ ] Deploy to staging environment
- [ ] Gather user feedback from engineering team
- [ ] Iterate on layout and metrics

---

## Success Metrics

After deployment, measure:

1. **Usage**: % of engineering team accessing dashboard daily
2. **Engagement**: Avg time spent on dashboard per session
3. **Utility**: NPS feedback on usefulness of metrics
4. **Action**: # of optimization issues filed based on dashboard insights
5. **Impact**: Correlation between dashboard insights and optimization ROI

---

**Dashboard Status**: Ready for implementation
**Created**: March 6, 2026
**Last Updated**: March 6, 2026
