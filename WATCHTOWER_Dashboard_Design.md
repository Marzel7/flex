# WATCHTOWER — Complete UI Design Specification

---

## 1. INFORMATION ARCHITECTURE

### Page Hierarchy

```
/watchtower/dashboard        ← NEW: primary intelligence dashboard (this spec)
/watchtower/intelligence     ← existing: predictions, methodology, evidence summary (keep)
/watchtower/campaigns        ← NEW: campaign-centric view
/watchtower/operator/<addr>  ← existing: individual operator detail (keep, restyle)
/network-diagram/watchtower  ← existing: full D3 graph (promote to embedded panel)
```

The new `/watchtower/dashboard` replaces the current `/watchtower/operational-intelligence` as the default landing page. The old tab-based layout is retired. The new design is a persistent three-column live console — no tabs, no page reload to switch views.

### Primary Navigation

In `base_shell.html` sidebar, the WATCHTOWER section becomes:

```
WATCHTOWER
  ▸ Dashboard          /watchtower/dashboard
  ▸ Campaigns          /watchtower/campaigns
  ▸ Intelligence       /watchtower/intelligence
  ▸ Network Graph      /network-diagram/watchtower
  ▸ Operator Detail    (navigated to contextually)
```

### Data Flow and Update Cadences

| Panel | Endpoint | Interval | Transport |
|---|---|---|---|
| Status bar | `/api/watchtower/live-metrics` | 30s | fetch poll |
| Event feed | `/api/watchtower/events-feed` (new) | 10s | fetch poll or SSE |
| Creator queue | `/api/watchtower/launch-candidates` | 20s | fetch poll |
| Topology graph | `/api/network-diagram/watchtower/live` | 60s initial, SSE updates | SSE |
| Campaign cards | `/api/watchtower/campaigns` (new) | 30s | fetch poll |
| Swarm panel | `/api/watchtower/swarms` (new) | 15s | fetch poll |
| Sweep timeline | `/api/watchtower/sweep-events` | 30s | fetch poll |
| HC queue (right) | `/api/watchtower/launch-candidates?min_score=85` | 10s | fetch poll |
| Operational status | `/api/watchtower/operational-status` | 60s | fetch poll |

---

## 2. GLOBAL STATUS BAR

### Visual Structure

The status bar is a fixed-height band (52px) pinned to the top of the content area (right of sidebar). It is NOT inside the scrollable container. It uses a single horizontal flex row with stat chips separated by 1px dividers.

```html
<header id="wt-status-bar">
  <div class="wt-sb-left">
    <span class="wt-sb-brand">WATCHTOWER</span>
    <span class="wt-sb-heat" id="sb-heat" data-level="0">COLD</span>
  </div>
  <div class="wt-sb-stats" id="wt-sb-stats">
    <div class="wt-sb-stat" id="sb-creator-cands">
      <span class="wt-sb-glyph" style="color:var(--clr-creator)">◈</span>
      <span class="wt-sb-val" id="sb-creator-cands-val">—</span>
      <span class="wt-sb-delta" id="sb-creator-cands-delta"></span>
      <span class="wt-sb-label">Creator Candidates</span>
    </div>
    <!-- repeat pattern for each stat -->
  </div>
  <div class="wt-sb-right">
    <span class="wt-sb-ts" id="sb-ts">—</span>
    <span class="wt-sb-dot" id="sb-live-dot"></span>
  </div>
</header>
```

### Stat Chip Definitions

Each chip: glyph + value + optional delta badge + label (2 lines: value large, label small).

| Stat | CSS var | Glyph | Alert condition | Alert color |
|---|---|---|---|---|
| Creator Candidates | `--clr-creator: #22d3ee` | `◈` | count > 0 | cyan |
| High Confidence Creators | `--clr-hc: #f97316` | `⬡` | count > 0 → pulse border | orange, animated |
| Active Trader Wallets | `--clr-trader: #3b82f6` | `⬢` | swarm count > 3 | blue |
| Active pAMM Campaigns | `--clr-pamm: #8b5cf6` | `◉` | count > 0 → red if >2 | purple→red |
| Sweep Epochs 24h | `--clr-sweep: #ef4444` | `↙` | any epoch | red |
| Reload Cycles 24h | `--clr-reload: #f59e0b` | `↺` | count > 0 | amber |
| Confirmed Launches 24h | `--clr-launch: #22c55e` | `⬆` | always green | green |
| New Sub-Provisioners | `--clr-subprov: #f97316` | `◆` | count > 0 → critical alert | orange, pulsing |
| Treasury Outflows 24h | `--clr-treasury: #eab308` | `⬡` | > 100 SOL = warning | gold |
| Operational Heat | computed | thermometer bar | ≥70 = red, ≥40 = amber | gradient |

### Delta Badge

```css
.wt-sb-delta {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'SF Mono', 'Fira Code', monospace;
}
.wt-sb-delta.up   { background: rgba(239,68,68,.2);  color: #ef4444; }
.wt-sb-delta.down { background: rgba(34,197,94,.15); color: #22c55e; }
```

Delta shows `+N 24h` or `-N 24h`. Positive delta on creators/traders is bad — shown red. Positive on launches is good — shown green.

### Operational Heat Indicator

Composite score 0–100 computed client-side from live-metrics response:

```javascript
function computeHeat(metrics) {
  let score = 0;
  if (metrics.hc_creator_count > 0)    score += 25;
  if (metrics.active_pamm_campaigns > 0) score += 30;
  if (metrics.sweep_epochs_24h > 0)    score += 20;
  if (metrics.new_subprov_24h > 0)     score += 15;
  if (metrics.treasury_out_24h_sol > 50) score += 10;
  return Math.min(score, 100);
}
```

Heat chip renders as a pill with color-coded background:

```css
.wt-sb-heat[data-level="cold"]     { background: rgba(139,148,158,.1); color: #8b949e; }
.wt-sb-heat[data-level="warm"]     { background: rgba(245,158,11,.12); color: #f59e0b; }
.wt-sb-heat[data-level="hot"]      { background: rgba(249,115,22,.15); color: #f97316; }
.wt-sb-heat[data-level="critical"] { background: rgba(239,68,68,.2);   color: #ef4444;
  animation: wt-heat-pulse 1.2s ease-in-out infinite; }
```

### Status Bar CSS

```css
#wt-status-bar {
  position: sticky;
  top: 0;
  z-index: 50;
  height: 52px;
  background: #0a0e13;
  border-bottom: 1px solid rgba(255,255,255,.07);
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.wt-sb-brand {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .12em;
  color: #22d3ee;
  text-transform: uppercase;
  white-space: nowrap;
  padding-right: 16px;
  border-right: 1px solid rgba(255,255,255,.07);
  margin-right: 12px;
}

.wt-sb-stats {
  display: flex;
  align-items: center;
  flex: 1;
  gap: 0;
  overflow: hidden;
}

.wt-sb-stat {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 0 14px;
  border-right: 1px solid rgba(255,255,255,.05);
  min-width: 90px;
  cursor: default;
}

.wt-sb-glyph {
  font-size: 10px;
  line-height: 1;
  margin-bottom: 1px;
}

.wt-sb-val {
  font-size: 16px;
  font-weight: 700;
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: var(--text-primary);
  line-height: 1;
}

.wt-sb-label {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: #6e7681;
  margin-top: 2px;
  white-space: nowrap;
}

.wt-sb-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: #22c55e;
  animation: wt-dot-pulse 2s ease-in-out infinite;
}
```

High-confidence alert state (any HC creators detected):

```css
#sb-hc-creators.has-alert .wt-sb-val {
  color: #f97316;
  text-shadow: 0 0 12px rgba(249,115,22,.6);
  animation: wt-hc-throb 1.5s ease-in-out infinite;
}
@keyframes wt-hc-throb {
  0%, 100% { text-shadow: 0 0 8px rgba(249,115,22,.4); }
  50%       { text-shadow: 0 0 20px rgba(249,115,22,.9); }
}
```

---

## 3. THREE-COLUMN MAIN LAYOUT

### Grid Definition

```css
#wt-main {
  display: grid;
  grid-template-columns: 280px 1fr 300px;
  grid-template-rows: 1fr;
  gap: 1px;
  height: calc(100vh - 52px); /* subtract status bar */
  background: rgba(255,255,255,.04); /* gap color = border-like lines */
  overflow: hidden;
}

#wt-feed-panel  { grid-column: 1; overflow-y: auto; background: #0a0e13; }
#wt-center      { grid-column: 2; display: flex; flex-direction: column; overflow: hidden; background: #0b0f14; }
#wt-queue-panel { grid-column: 3; overflow-y: auto; background: #0a0e13; }
```

At 1440px viewport (with 180px sidebar), the effective widths are:
- Left feed: 280px
- Center: ~680px
- Right queue: 300px

### Panel Header Pattern

Every panel uses a consistent header strip:

```html
<div class="wt-panel-hdr">
  <span class="wt-panel-title">LIVE EVENTS</span>
  <span class="wt-panel-controls">
    <button class="wt-ctrl-btn" title="Filter">⚙</button>
    <span class="wt-refresh-ring" id="feed-refresh-ring"></span>
  </span>
</div>
```

```css
.wt-panel-hdr {
  height: 36px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255,255,255,.05);
  flex-shrink: 0;
}
.wt-panel-title {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: #6e7681;
}
.wt-ctrl-btn {
  background: none;
  border: none;
  color: #6e7681;
  cursor: pointer;
  font-size: 12px;
  padding: 2px 4px;
  border-radius: 3px;
}
.wt-ctrl-btn:hover { color: #e6edf3; background: rgba(255,255,255,.05); }
```

Refresh ring is a thin circular CSS animation that triggers on each poll cycle — a 1s arc sweep, not a spinning loader.

```css
.wt-refresh-ring {
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 1.5px solid rgba(255,255,255,.1);
  border-top-color: #22d3ee;
  display: inline-block;
}
.wt-refresh-ring.spinning {
  animation: wt-ring-spin .8s linear;
}
@keyframes wt-ring-spin { to { transform: rotate(360deg); } }
```

---

## 4. REAL-TIME EVENT FEED (left panel)

### Semantic Event Type Definitions

| Event Type | Badge Color | Background | Glyph | Label |
|---|---|---|---|---|
| `CREATOR_CANDIDATE` | `#22d3ee` | `rgba(34,211,238,.1)` | `◈` | CANDIDATE |
| `HIGH_CONFIDENCE_CREATOR` | `#f97316` | `rgba(249,115,22,.15)` | `⬡` | HIGH CONF |
| `TRADER_SWARM_DETECTED` | `#3b82f6` | `rgba(59,130,246,.12)` | `⬢` | SWARM |
| `PAMM_CAMPAIGN_ACTIVE` | `#8b5cf6` | `rgba(139,92,246,.12)` | `◉` | pAMM ACTIVE |
| `PROFIT_SWEEP_EPOCH` | `#ef4444` | `rgba(239,68,68,.15)` | `↙` | SWEEP |
| `RELOAD_CYCLE` | `#f59e0b` | `rgba(245,158,11,.12)` | `↺` | RELOAD |
| `PUMPFUN_CREATE_CONFIRMED` | `#22c55e` | `rgba(34,197,94,.12)` | `⬆` | LAUNCHED |
| `NEW_SUBPROVISIONER` | `#f97316` | `rgba(249,115,22,.15)` | `◆` | NEW SUB-PROV |
| `TREASURY_FANOUT` | `#eab308` | `rgba(234,179,8,.12)` | `⬡` | TREASURY |
| `STATE_CHANGE` | `#8b949e` | `rgba(139,148,158,.08)` | `→` | STATE |

### Event Item HTML Pattern

```html
<div class="wt-event-item" data-type="HIGH_CONFIDENCE_CREATOR" data-id="evt-123" data-campaign="camp-07">
  <div class="wt-evt-header">
    <span class="wt-evt-badge ev-HIGH_CONFIDENCE_CREATOR">⬡ HIGH CONF</span>
    <span class="wt-evt-campaign-tag">CAMP-07</span>
    <span class="wt-evt-ts">14:22:07</span>
  </div>
  <div class="wt-evt-body">
    <span class="wt-entity-chip wt-chip-creator" data-address="Abc1...xyz9" title="Abc1...full...xyz9">
      Abc1…xyz9
    </span>
    scored <span class="wt-score-inline score-hc">91</span>
    via <span class="wt-addr-ref">44or…WS68</span>
  </div>
  <div class="wt-evt-meta">
    <span class="wt-conf-meter">
      <span class="wt-conf-fill" style="width:91%"></span>
    </span>
    <span class="wt-evt-severity sev-critical">●</span>
  </div>
  <div class="wt-evt-actions">
    <button class="wt-evt-ack" title="Acknowledge">✓</button>
    <button class="wt-evt-view" title="View in graph">⬡</button>
  </div>
</div>
```

### Event Item CSS

```css
.wt-event-item {
  padding: 9px 12px;
  border-bottom: 1px solid rgba(255,255,255,.04);
  cursor: pointer;
  transition: background .1s;
  position: relative;
}
.wt-event-item:hover { background: rgba(255,255,255,.025); }
.wt-event-item.highlighted { background: rgba(34,211,238,.06); border-left: 2px solid #22d3ee; }
.wt-event-item.acknowledged { opacity: .45; }

.wt-evt-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 5px;
}

.wt-evt-badge {
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .06em;
  padding: 2px 6px;
  border-radius: 3px;
  white-space: nowrap;
  text-transform: uppercase;
}

/* Badge color declarations */
.ev-CREATOR_CANDIDATE      { background: rgba(34,211,238,.12); color: #22d3ee; border: 1px solid rgba(34,211,238,.25); }
.ev-HIGH_CONFIDENCE_CREATOR{ background: rgba(249,115,22,.15); color: #f97316; border: 1px solid rgba(249,115,22,.3);
  animation: wt-badge-pulse-orange 2s ease-in-out infinite; }
.ev-TRADER_SWARM_DETECTED  { background: rgba(59,130,246,.12); color: #3b82f6; border: 1px solid rgba(59,130,246,.25); }
.ev-PAMM_CAMPAIGN_ACTIVE   { background: rgba(139,92,246,.12); color: #8b5cf6; border: 1px solid rgba(139,92,246,.25); }
.ev-PROFIT_SWEEP_EPOCH     { background: rgba(239,68,68,.15);  color: #ef4444; border: 1px solid rgba(239,68,68,.3); }
.ev-RELOAD_CYCLE           { background: rgba(245,158,11,.12); color: #f59e0b; border: 1px solid rgba(245,158,11,.25); }
.ev-PUMPFUN_CREATE_CONFIRMED{ background: rgba(34,197,94,.12); color: #22c55e; border: 1px solid rgba(34,197,94,.25); }
.ev-NEW_SUBPROVISIONER     { background: rgba(249,115,22,.18); color: #f97316; border: 1px solid rgba(249,115,22,.4);
  animation: wt-badge-pulse-orange 1s ease-in-out infinite; }
.ev-TREASURY_FANOUT        { background: rgba(234,179,8,.12);  color: #eab308; border: 1px solid rgba(234,179,8,.25); }

.wt-evt-campaign-tag {
  font-size: 9px;
  color: #8b5cf6;
  background: rgba(139,92,246,.1);
  padding: 1px 5px;
  border-radius: 2px;
  font-family: 'SF Mono', monospace;
}

.wt-evt-ts {
  font-size: 10px;
  color: #6e7681;
  font-family: 'SF Mono', monospace;
  margin-left: auto;
}

.wt-evt-body {
  font-size: 11px;
  color: #8b949e;
  line-height: 1.5;
  margin-bottom: 4px;
}

/* Entity chip — clickable wallet address */
.wt-entity-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  cursor: pointer;
  transition: background .1s;
}
.wt-chip-creator { color: #22d3ee; background: rgba(34,211,238,.08); border: 1px solid rgba(34,211,238,.2); }
.wt-chip-trader  { color: #3b82f6; background: rgba(59,130,246,.08); border: 1px solid rgba(59,130,246,.2); }
.wt-chip-subprov { color: #f97316; background: rgba(249,115,22,.08); border: 1px solid rgba(249,115,22,.2); }
.wt-chip-treasury{ color: #eab308; background: rgba(234,179,8,.08);  border: 1px solid rgba(234,179,8,.2); }
.wt-entity-chip:hover { background: rgba(255,255,255,.1); }

/* Inline score */
.wt-score-inline {
  font-family: 'SF Mono', monospace;
  font-size: 11px;
  font-weight: 700;
}
.wt-score-inline.score-candidate { color: #f59e0b; }
.wt-score-inline.score-hc        { color: #f97316; }

/* Confidence meter strip */
.wt-conf-meter {
  display: inline-block;
  width: 60px;
  height: 3px;
  background: rgba(255,255,255,.07);
  border-radius: 2px;
  overflow: hidden;
  vertical-align: middle;
}
.wt-conf-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #22d3ee);
  border-radius: 2px;
  transition: width .3s;
}

/* Severity dot */
.wt-evt-severity { font-size: 8px; }
.sev-info     { color: #6e7681; }
.sev-warning  { color: #f59e0b; }
.sev-alert    { color: #f97316; }
.sev-critical { color: #ef4444; animation: wt-sev-blink 1s step-end infinite; }
@keyframes wt-sev-blink { 50% { opacity: 0; } }

/* Action buttons */
.wt-evt-actions {
  display: none;
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  gap: 4px;
}
.wt-event-item:hover .wt-evt-actions { display: flex; }
.wt-evt-ack, .wt-evt-view {
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.1);
  color: #8b949e;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  cursor: pointer;
}
.wt-evt-ack:hover  { color: #22c55e; }
.wt-evt-view:hover { color: #22d3ee; }
```

### Temporal Clustering

Events within 30 seconds of each other are visually grouped under a collapsible cluster header:

```html
<div class="wt-evt-cluster">
  <div class="wt-cluster-hdr" onclick="toggleCluster(this)">
    <span class="wt-cluster-time">14:22:04–14:22:31</span>
    <span class="wt-cluster-count">3 events</span>
    <span class="wt-cluster-toggle">▾</span>
  </div>
  <div class="wt-cluster-body">
    <!-- event items -->
  </div>
</div>
```

### Feed Entry Animation

```css
@keyframes wt-feed-enter {
  from {
    opacity: 0;
    transform: translateX(-8px);
    background: rgba(34,211,238,.08);
  }
  to {
    opacity: 1;
    transform: translateX(0);
    background: transparent;
  }
}
.wt-event-item.new-entry {
  animation: wt-feed-enter .4s ease-out forwards;
}
```

Events that are HIGH_CONFIDENCE_CREATOR or NEW_SUBPROVISIONER use a longer, more visible entry:

```css
.wt-event-item.new-entry.critical-entry {
  animation: wt-feed-enter-critical .6s ease-out forwards;
}
@keyframes wt-feed-enter-critical {
  from { opacity: 0; transform: translateX(-8px); background: rgba(249,115,22,.2); }
  40%  { background: rgba(249,115,22,.12); }
  to   { opacity: 1; transform: translateX(0); background: transparent; }
}
```

JavaScript prepends new items via `feed.insertBefore(item, feed.firstChild)` after creating the element, adding the `new-entry` class, then removing it after 800ms.

### New Endpoint Required

`/api/watchtower/events-feed` — returns a chronological list of semantic alert events, not raw transactions. This is a new endpoint that consolidates from `watchtower_events`, `wt_discovery_log`, `watchtower_sweep_events`, and the scoring pipeline into a unified alert stream. Response shape:

```json
{
  "events": [
    {
      "id": "evt-uuid",
      "type": "HIGH_CONFIDENCE_CREATOR",
      "severity": "critical",
      "ts": 1748000000,
      "entity": { "address": "Abc1...xyz9", "type": "creator", "score": 91 },
      "campaign_id": "camp-07",
      "sub_prov": "44or...WS68",
      "detail": "Score 91/100 — funding lineage confirmed, AMM absent, amount fingerprint match",
      "payload": {}
    }
  ],
  "since": 1748000000,
  "total": 47
}
```

---

## 5. CREATOR INTELLIGENCE PANEL (center — top strip)

This is a horizontal scrollable strip of creator candidate cards across the top of the center column, above the main graph. Height: 140px fixed.

```html
<div id="wt-creator-strip">
  <div class="wt-panel-hdr">
    <span class="wt-panel-title">CREATOR CANDIDATES</span>
    <span class="wt-strip-count" id="cand-count">0 pending</span>
  </div>
  <div class="wt-creator-scroll" id="creator-scroll">
    <!-- creator cards inserted here -->
  </div>
</div>
```

```css
#wt-creator-strip {
  flex-shrink: 0;
  border-bottom: 1px solid rgba(255,255,255,.05);
}

.wt-creator-scroll {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,.1) transparent;
  height: 104px;
  align-items: stretch;
}
```

### Creator Card

```html
<div class="wt-creator-card" data-address="Abc1...xyz9" data-score="91">
  <div class="wt-cc-header">
    <span class="wt-cc-state-badge state-HIGH_CONFIDENCE">HIGH CONF</span>
    <span class="wt-cc-timer" id="cc-timer-abc1">00:04:17</span>
  </div>
  <div class="wt-cc-addr">Abc1…xyz9</div>
  <div class="wt-cc-score-bar">
    <div class="wt-cc-score-fill" style="width:91%;background:var(--clr-score-hc)"></div>
    <span class="wt-cc-score-val">91</span>
  </div>
  <div class="wt-cc-components">
    <span class="wt-cc-comp" title="Funding lineage">L:9</span>
    <span class="wt-cc-comp" title="Amount fingerprint">F:10</span>
    <span class="wt-cc-comp" title="AMM absence">A:10</span>
    <span class="wt-cc-comp" title="Freshness">N:9</span>
  </div>
  <div class="wt-cc-actions">
    <button class="wt-cc-btn" onclick="enrollMonitoring('Abc1...xyz9')">Monitor</button>
    <button class="wt-cc-btn wt-cc-btn-graph" onclick="showInGraph('Abc1...xyz9')">Graph</button>
  </div>
</div>
```

```css
.wt-creator-card {
  flex-shrink: 0;
  width: 180px;
  background: #12171f;
  border: 1px solid rgba(255,255,255,.07);
  border-radius: 6px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  cursor: pointer;
  transition: border-color .15s;
}
.wt-creator-card:hover { border-color: rgba(34,211,238,.3); }
.wt-creator-card[data-score-level="hc"] { border-color: rgba(249,115,22,.3); }

.wt-cc-header { display: flex; justify-content: space-between; align-items: center; }

.wt-cc-state-badge {
  font-size: 8px; font-weight: 800; letter-spacing: .07em;
  padding: 1px 5px; border-radius: 2px; text-transform: uppercase;
}
.state-CREATOR_CANDIDATE { background: rgba(34,211,238,.1);  color: #22d3ee; }
.state-HIGH_CONFIDENCE   { background: rgba(249,115,22,.15); color: #f97316;
  animation: wt-badge-pulse-orange 2s infinite; }
.state-LAUNCHED          { background: rgba(34,197,94,.12);  color: #22c55e; }
.state-ABANDONED         { background: rgba(107,114,128,.1); color: #6b7280; }

.wt-cc-timer {
  font-family: 'SF Mono', monospace;
  font-size: 10px;
  color: #f59e0b;
}
/* Timer turns red when < 60s (urgency) */
.wt-cc-timer.urgent { color: #ef4444; animation: wt-sev-blink 1s step-end infinite; }

.wt-cc-addr {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 10px;
  color: #22d3ee;
}

.wt-cc-score-bar {
  display: flex; align-items: center; gap: 6px;
  height: 6px; background: rgba(255,255,255,.06); border-radius: 3px; overflow: hidden;
  position: relative;
}
.wt-cc-score-fill {
  height: 100%; border-radius: 3px; transition: width .4s;
}
/* Score fill colors */
:root {
  --clr-score-low:  #6b7280;  /* <60 */
  --clr-score-cand: #f59e0b;  /* 60-84 */
  --clr-score-hc:   #f97316;  /* 85+ */
}
.wt-cc-score-val {
  position: absolute; right: 0; top: -10px;
  font-size: 10px; font-weight: 700;
  font-family: 'SF Mono', monospace;
}

.wt-cc-components {
  display: flex; gap: 4px; flex-wrap: wrap;
}
.wt-cc-comp {
  font-size: 9px; font-family: 'SF Mono', monospace;
  color: #6e7681; background: rgba(255,255,255,.04);
  padding: 1px 4px; border-radius: 2px;
}

.wt-cc-actions { display: flex; gap: 4px; margin-top: auto; }
.wt-cc-btn {
  flex: 1; font-size: 9px; padding: 3px 0; border-radius: 3px;
  border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.04);
  color: #8b949e; cursor: pointer;
}
.wt-cc-btn:hover { border-color: rgba(34,211,238,.4); color: #22d3ee; }
.wt-cc-btn-graph:hover { border-color: rgba(139,92,246,.4); color: #8b5cf6; }
```

Score bar color is set by JS based on score value:
```javascript
function scoreColor(s) {
  if (s >= 85) return 'var(--clr-score-hc)';
  if (s >= 60) return 'var(--clr-score-cand)';
  return 'var(--clr-score-low)';
}
```

---

## 6. TOPOLOGY GRAPH (center — main)

The D3 force-directed graph occupies the remaining center height below the creator strip. It fills 100% of available space.

### Node Type Definitions

| Node Type | Color | Radius | Shape | CSS class |
|---|---|---|---|---|
| TREASURY | `#eab308` (gold) | 22px | hexagon (D3 symbol) | `node-treasury` |
| SUB_PROV | `#f97316` (orange) | 16px | diamond | `node-subprov` |
| RELAY | `#f59e0b` (amber) | 10px | circle | `node-relay` |
| CREATOR | `#22d3ee` (cyan) | 12px | circle | `node-creator` |
| CREATOR (HC) | `#f97316` | 14px | circle + outer ring | `node-creator-hc` |
| TRADER_SWARM | `#3b82f6` (blue) | cluster of 5px dots | circle cluster | `node-swarm` |
| TOKEN | `#22c55e` (green) | 8px | square | `node-token` |
| SWEEP_COLLECTOR | `#ef4444` (red) | 10px | inverted triangle | `node-sweep` |

### Edge Type Definitions

| Edge | Color | Style | Width |
|---|---|---|---|
| `funded_by` | `rgba(255,255,255,.3)` | solid | 1.5px |
| `traded` | `rgba(59,130,246,.5)` | dashed | 1px |
| `swept_to` | `rgba(239,68,68,.6)` | solid | 2px |
| `created` | `rgba(34,197,94,.5)` | solid | 1.5px |
| `provisioned_by` | `rgba(249,115,22,.4)` | dotted | 1px |

### D3 Initialization Pseudocode

```javascript
const wt_graph = {
  svg: null,
  simulation: null,
  nodes: [],
  edges: [],

  init(containerId) {
    const el = document.getElementById(containerId);
    const W = el.clientWidth, H = el.clientHeight;

    this.svg = d3.select(`#${containerId}`)
      .append('svg')
      .attr('width', W).attr('height', H);

    // Defs: arrowheads, glow filters
    const defs = this.svg.append('defs');
    ['funded_by','swept_to','created','traded'].forEach(type => {
      defs.append('marker')
        .attr('id', `arrow-${type}`)
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 20).attr('refY', 0)
        .attr('markerWidth', 6).attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path').attr('d', 'M0,-5L10,0L0,5')
        .attr('fill', edgeColor(type));
    });

    // Glow filter for treasury/HC nodes
    const glow = defs.append('filter').attr('id', 'node-glow');
    glow.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'blur');
    glow.append('feMerge').selectAll('feMergeNode')
      .data(['blur','SourceGraphic']).enter()
      .append('feMergeNode').attr('in', d => d);

    // Background click to deselect
    this.svg.append('rect')
      .attr('width', W).attr('height', H)
      .attr('fill', 'transparent')
      .on('click', () => this.deselectAll());

    // Layers: edges, particles, nodes, labels
    this.edgeLayer     = this.svg.append('g').attr('class', 'wt-edge-layer');
    this.particleLayer = this.svg.append('g').attr('class', 'wt-particle-layer');
    this.nodeLayer     = this.svg.append('g').attr('class', 'wt-node-layer');
    this.labelLayer    = this.svg.append('g').attr('class', 'wt-label-layer');

    this.simulation = d3.forceSimulation()
      .force('link',    d3.forceLink().id(d => d.id).distance(d => linkDistance(d)))
      .force('charge',  d3.forceManyBody().strength(d => nodeCharge(d)))
      .force('center',  d3.forceCenter(W/2, H/2))
      .force('collide', d3.forceCollide().radius(d => nodeRadius(d) + 8));
  },

  update(graphData) {
    this.nodes = graphData.nodes;
    this.edges = graphData.edges;
    this.render();
  },

  render() {
    // Edge selection
    const edges = this.edgeLayer.selectAll('.wt-edge')
      .data(this.edges, d => d.id)
      .join(
        enter => enter.append('line')
          .attr('class', d => `wt-edge edge-${d.type}`)
          .attr('stroke', d => edgeColor(d.type))
          .attr('stroke-width', d => edgeWidth(d.type))
          .attr('stroke-dasharray', d => edgeDash(d.type))
          .attr('marker-end', d => `url(#arrow-${d.type})`)
          .attr('opacity', 0).call(e => e.transition().duration(400).attr('opacity', 1)),
        update => update,
        exit => exit.transition().duration(300).attr('opacity', 0).remove()
      );

    // Node selection
    const nodes = this.nodeLayer.selectAll('.wt-node')
      .data(this.nodes, d => d.id)
      .join(
        enter => enter.append('circle')
          .attr('class', d => `wt-node node-${d.type.toLowerCase()}`)
          .attr('r', d => nodeRadius(d))
          .attr('fill', d => nodeFill(d))
          .attr('stroke', d => nodeStroke(d))
          .attr('stroke-width', d => d.type === 'TREASURY' ? 2 : 1.5)
          .attr('filter', d => ['TREASURY','CREATOR_HC','SUB_PROV'].includes(d.type) ? 'url(#node-glow)' : null)
          .attr('opacity', d => d.confidence ?? 1)
          .call(d3.drag()
            .on('start', (event, d) => { if (!event.active) this.simulation.alphaTarget(.3).restart(); d.fx = d.x; d.fy = d.y; })
            .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
            .on('end',   (event, d) => { if (!event.active) this.simulation.alphaTarget(0); d.fx = null; d.fy = null; })
          )
          .on('click',     (event, d) => this.onNodeClick(d))
          .on('mouseover', (event, d) => this.showTooltip(event, d))
          .on('mouseout',  ()          => this.hideTooltip()),
        update => update
          .attr('opacity', d => d.confidence ?? 1)
          .attr('r', d => nodeRadius(d)),
        exit => exit.transition().duration(300).attr('r', 0).remove()
      );

    this.simulation.nodes(this.nodes).on('tick', () => {
      edges.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
           .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
      nodes.attr('cx', d=>d.x).attr('cy', d=>d.y);
      this.labelLayer.selectAll('.wt-node-label')
        .attr('x', d=>d.x).attr('y', d=>d.y + nodeRadius(d) + 12);
    });
    this.simulation.force('link').links(this.edges);
    this.simulation.alpha(.5).restart();
  }
};
```

### Node Color Functions

```javascript
const NODE_COLORS = {
  TREASURY:      { fill: '#eab308', stroke: '#fde047' },
  SUB_PROV:      { fill: '#f97316', stroke: '#fb923c' },
  RELAY:         { fill: '#f59e0b', stroke: '#fbbf24' },
  CREATOR:       { fill: '#22d3ee', stroke: '#67e8f9' },
  CREATOR_HC:    { fill: '#f97316', stroke: '#ef4444' },
  TRADER_SWARM:  { fill: '#3b82f6', stroke: '#60a5fa' },
  TOKEN:         { fill: '#22c55e', stroke: '#4ade80' },
  SWEEP_COLLECTOR:{ fill: '#ef4444', stroke: '#f87171' },
};
function nodeFill(d)   { return (NODE_COLORS[d.type] || NODE_COLORS.CREATOR).fill; }
function nodeStroke(d) { return (NODE_COLORS[d.type] || NODE_COLORS.CREATOR).stroke; }
function nodeRadius(d) {
  const base = { TREASURY:22, SUB_PROV:16, CREATOR_HC:14, CREATOR:12, RELAY:10,
                 SWEEP_COLLECTOR:10, TRADER_SWARM:8, TOKEN:8 };
  return (base[d.type] || 10) * (d.highlighted ? 1.4 : 1);
}
```

### Edge Particle Animation

Fund flow direction is conveyed by moving particles along edges. Uses D3 timer + path position interpolation:

```javascript
function startEdgeParticles(svg, edges, nodeMap) {
  const PARTICLE_COLORS = {
    funded_by: '#ffffff',
    swept_to:  '#ef4444',
    created:   '#22c55e',
    traded:    '#3b82f6',
  };

  const particles = [];
  edges.forEach(e => {
    if (!['funded_by','swept_to'].includes(e.type)) return;
    particles.push({ edge: e, t: Math.random(), speed: 0.004 + Math.random() * 0.003 });
  });

  const particleGroup = svg.select('.wt-particle-layer');
  const circles = particleGroup.selectAll('.wt-particle')
    .data(particles).join('circle')
    .attr('class', 'wt-particle')
    .attr('r', 2.5)
    .attr('fill', d => PARTICLE_COLORS[d.edge.type] || '#fff')
    .attr('opacity', .7);

  d3.timer(() => {
    particles.forEach(p => {
      p.t = (p.t + p.speed) % 1;
      const src = p.edge.source, tgt = p.edge.target;
      p.x = src.x + (tgt.x - src.x) * p.t;
      p.y = src.y + (tgt.y - src.y) * p.t;
    });
    circles.attr('cx', d => d.x).attr('cy', d => d.y);
  });
}
```

### Graph Controls Bar

Above the SVG, a thin toolbar:

```html
<div id="wt-graph-controls">
  <button class="wt-gctrl" onclick="graph.resetZoom()" title="Reset view">⊞</button>
  <button class="wt-gctrl" onclick="graph.toggleParticles()" title="Toggle flow particles">≈</button>
  <select class="wt-gctrl-select" id="graph-campaign-filter" onchange="graph.filterByCampaign(this.value)">
    <option value="">All campaigns</option>
    <!-- populated from API -->
  </select>
  <label class="wt-gctrl-label">
    <input type="range" id="graph-time-filter" min="0" max="100" value="100" oninput="graph.filterByTime(this.value)">
    <span id="graph-time-label">All time</span>
  </label>
  <span class="wt-gctrl-sep"></span>
  <span class="wt-graph-legend-mini">
    <span class="wt-gl-dot" style="background:#eab308"></span>Treasury
    <span class="wt-gl-dot" style="background:#f97316"></span>Sub-Prov
    <span class="wt-gl-dot" style="background:#22d3ee"></span>Creator
    <span class="wt-gl-dot" style="background:#3b82f6"></span>Swarm
    <span class="wt-gl-dot" style="background:#22c55e"></span>Token
    <span class="wt-gl-dot" style="background:#ef4444"></span>Sweep
  </span>
</div>
```

### Campaign Isolation

When user selects a campaign from the dropdown, `graph.filterByCampaign(id)` dims all nodes/edges not belonging to that campaign to opacity 0.1 over a 400ms transition, and highlights the campaign's subgraph.

### Node Click Behavior

```javascript
onNodeClick(d) {
  // 1. Highlight this node and its 1-hop neighbors
  this.highlightNeighborhood(d.id);
  // 2. Highlight matching events in the left feed
  document.querySelectorAll(`.wt-event-item`).forEach(el => {
    el.classList.toggle('highlighted', el.dataset.entityAddress === d.id);
  });
  // 3. Open right drawer with entity detail
  openEntityDrawer(d);
}
```

---

## 7. CAMPAIGN INTELLIGENCE PANEL

Campaign cards live in the right column, below the HC queue. On the campaigns page (`/watchtower/campaigns`), they occupy the full center.

### Campaign Card HTML

```html
<div class="wt-campaign-card" data-campaign-id="camp-07" data-phase="TRADERS_DEPLOYED">
  <div class="wt-camp-header">
    <div>
      <span class="wt-camp-id">CAMP-07</span>
      <span class="wt-entity-chip wt-chip-subprov">44or…WS68</span>
    </div>
    <span class="wt-camp-phase-badge phase-TRADERS_DEPLOYED">DEPLOYED</span>
  </div>

  <!-- Phase progress bar -->
  <div class="wt-camp-progress-track">
    <div class="wt-camp-progress-fill" style="width:60%"></div>
    <div class="wt-camp-phases">
      <span class="ph done">PROV</span>
      <span class="ph done">LIVE</span>
      <span class="ph active">TRADE</span>
      <span class="ph">AMM</span>
      <span class="ph">SWEEP</span>
      <span class="ph">DONE</span>
    </div>
  </div>

  <div class="wt-camp-stats">
    <div class="wt-camp-stat"><span class="val">3</span><span class="lbl">Creators</span></div>
    <div class="wt-camp-stat"><span class="val">847</span><span class="lbl">Traders</span></div>
    <div class="wt-camp-stat"><span class="val">2</span><span class="lbl">Tokens</span></div>
    <div class="wt-camp-stat"><span class="val orange">142 SOL</span><span class="lbl">Deployed</span></div>
    <div class="wt-camp-stat"><span class="val red">0 SOL</span><span class="lbl">Swept</span></div>
  </div>

  <!-- Mini timeline sparkline (Chart.js or D3 inline) -->
  <canvas class="wt-camp-sparkline" id="camp-07-spark" height="32"></canvas>

  <div class="wt-camp-heat">
    <span class="wt-heat-label">HEAT</span>
    <div class="wt-heat-track">
      <div class="wt-heat-fill" style="width:72%;background:var(--clr-score-hc)"></div>
    </div>
    <span class="wt-heat-val">72</span>
  </div>
</div>
```

### Campaign Phase Colors

```css
.phase-PROVISIONING    { background: rgba(107,114,128,.1);  color: #6b7280; }
.phase-CREATOR_LIVE    { background: rgba(34,211,238,.12);  color: #22d3ee; }
.phase-TRADERS_DEPLOYED{ background: rgba(59,130,246,.12);  color: #3b82f6; }
.phase-AMM_ACTIVE      { background: rgba(139,92,246,.15);  color: #8b5cf6; }
.phase-SWEEPING        { background: rgba(239,68,68,.15);   color: #ef4444; }
.phase-COMPLETE        { background: rgba(34,197,94,.1);    color: #22c55e; }
```

### Campaign State Transition Animation

When phase changes, the badge and progress bar transition over 600ms:

```css
.wt-camp-phase-badge {
  transition: background .6s, color .6s, border-color .6s;
}
.wt-camp-progress-fill {
  transition: width .8s cubic-bezier(.4, 0, .2, 1), background .6s;
}
```

---

## 8. TRADER SWARM PANEL

Swarm cards are shown in the right column or in a dedicated section of the center panel when campaign view is active.

### Swarm Card HTML

```html
<div class="wt-swarm-card" data-swarm-id="swarm-042">
  <div class="wt-swarm-header">
    <div class="wt-swarm-size-ring" data-count="847">
      <svg width="48" height="48" viewBox="0 0 48 48">
        <circle cx="24" cy="24" r="20" fill="none" stroke="rgba(59,130,246,.15)" stroke-width="4"/>
        <circle cx="24" cy="24" r="20" fill="none" stroke="#3b82f6" stroke-width="4"
          stroke-dasharray="125.6" stroke-dashoffset="37.7"   <!-- 70% active -->
          stroke-linecap="round" transform="rotate(-90 24 24)"/>
        <text x="24" y="28" text-anchor="middle" font-size="12" fill="#3b82f6" font-weight="700">847</text>
      </svg>
    </div>
    <div class="wt-swarm-meta">
      <div class="wt-swarm-id">SWARM-042</div>
      <div class="wt-swarm-state-badge state-ACTIVE">ACTIVE</div>
      <div class="wt-swarm-sync">
        <span class="wt-sync-label">SYNC</span>
        <div class="wt-sync-bar-track">
          <div class="wt-sync-fill" style="width:88%"></div>
        </div>
        <span class="wt-sync-val">88%</span>
      </div>
    </div>
  </div>

  <div class="wt-swarm-token">
    <span class="wt-entity-chip wt-chip-token">$SAMPLE</span>
    <span class="wt-swarm-vol">Vol: 142 SOL</span>
    <span class="wt-swarm-last">Last burst: 4m ago</span>
  </div>

  <div class="wt-swarm-metrics">
    <div class="wt-sm"><span class="val red">12.4 SOL/h</span><span class="lbl">Sweep Vel.</span></div>
    <div class="wt-sm"><span class="val orange">3x</span><span class="lbl">Reloads</span></div>
    <div class="wt-sm"><span class="val">CAMP-07</span><span class="lbl">Campaign</span></div>
  </div>
</div>
```

### Swarm State Ring

The outer ring of the SVG fills proportionally to active wallet ratio. Dormant swarms show the ring with reduced opacity and a gray fill:

```css
.wt-swarm-card[data-state="DORMANT"] .wt-swarm-size-ring circle:last-child {
  stroke: #6b7280;
  opacity: .4;
}
.wt-swarm-card[data-state="ACTIVE"] .wt-swarm-size-ring circle:last-child {
  stroke: #3b82f6;
  animation: wt-ring-glow 2s ease-in-out infinite;
}
@keyframes wt-ring-glow {
  0%,100% { filter: drop-shadow(0 0 3px rgba(59,130,246,.4)); }
  50%      { filter: drop-shadow(0 0 8px rgba(59,130,246,.8)); }
}
```

### Sweep Epoch Flash

When a PROFIT_SWEEP_EPOCH event fires, the swarm card triggers a red pulse:

```javascript
function flashSweepEpoch(swarmId) {
  const card = document.querySelector(`.wt-swarm-card[data-swarm-id="${swarmId}"]`);
  if (!card) return;
  card.classList.add('sweep-flash');
  setTimeout(() => card.classList.remove('sweep-flash'), 1500);
}
```

```css
@keyframes wt-sweep-flash {
  0%   { background: rgba(239,68,68,.25); border-color: rgba(239,68,68,.5); }
  30%  { background: rgba(239,68,68,.15); }
  100% { background: transparent; border-color: rgba(255,255,255,.07); }
}
.wt-swarm-card.sweep-flash {
  animation: wt-sweep-flash 1.5s ease-out forwards;
}
```

---

## 9. SWEEP / RELOAD INTELLIGENCE

Presented as a horizontal epoch timeline at the bottom of the right column, or as a dedicated section in the center panel.

### Epoch Timeline HTML

```html
<div id="wt-sweep-timeline">
  <div class="wt-panel-hdr">
    <span class="wt-panel-title">SWEEP TIMELINE — 24H</span>
    <span class="wt-sweep-total" id="sweep-24h-total">0 SOL</span>
  </div>
  <div class="wt-epoch-track" id="epoch-track">
    <!-- epoch bars positioned absolutely by JS based on timestamp -->
  </div>
  <div class="wt-epoch-axis">
    <span>-24h</span><span>-18h</span><span>-12h</span><span>-6h</span><span>now</span>
  </div>
</div>
```

```css
.wt-epoch-track {
  position: relative;
  height: 40px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.06);
  border-radius: 4px;
  margin: 8px 12px;
  overflow: hidden;
}

.wt-epoch-bar {
  position: absolute;
  top: 4px; bottom: 4px;
  background: rgba(239,68,68,.4);
  border: 1px solid rgba(239,68,68,.6);
  border-radius: 3px;
  cursor: pointer;
  transition: background .15s;
  min-width: 3px;
}
.wt-epoch-bar:hover { background: rgba(239,68,68,.7); }

.wt-reload-bar {
  position: absolute;
  top: 0; height: 4px;
  background: rgba(245,158,11,.6);
  border-radius: 2px;
}
```

Epoch bar width is proportional to its duration. Position is computed as `((epochStart - windowStart) / windowDuration) * 100 + '%'`.

On hover, a tooltip shows: wallet count, SOL swept, duration. Reload overlay bars appear at the position where the provisioner re-funded after each epoch.

---

## 10. HIGH-CONFIDENCE CREATOR QUEUE (right panel — top)

This is the highest-priority real-estate on the dashboard. It anchors the top of the right column.

### Queue HTML

```html
<div id="wt-hc-queue">
  <div class="wt-panel-hdr">
    <span class="wt-panel-title">HIGH CONFIDENCE</span>
    <span class="wt-hc-count" id="hc-count">0</span>
  </div>
  <div id="hc-queue-list">
    <!-- populated by JS -->
  </div>
  <div class="section-empty" id="hc-empty" style="display:none">No high-confidence creators at this time</div>
</div>
```

### HC Queue Item HTML

```html
<div class="wt-hc-item" data-address="Abc1...xyz9" id="hc-Abc1xyz9">
  <div class="wt-hc-score-badge">91</div>
  <div class="wt-hc-body">
    <div class="wt-hc-addr-row">
      <span class="wt-entity-chip wt-chip-creator">Abc1…xyz9</span>
      <button class="wt-copy-btn" onclick="copyAddr('Abc1...full...xyz9')" title="Copy full address">⎘</button>
    </div>
    <div class="wt-hc-meta">
      via <span class="wt-entity-chip wt-chip-subprov">44or…WS68</span>
      <span class="wt-hc-amount">1.003928 SOL</span>
      <span class="wt-fingerprint-match fp-yes" title="Exact fingerprint match">FP ✓</span>
    </div>
    <div class="wt-hc-timer-row">
      <span class="wt-hc-age">funded <span class="wt-hc-since" data-ts="1748000000">4m 17s ago</span></span>
      <div class="wt-urgency-bar"><div class="wt-urgency-fill" style="width:15%"></div></div>
    </div>
  </div>
  <div class="wt-hc-actions">
    <button class="wt-hc-btn wt-hc-monitor" onclick="markMonitored('Abc1...xyz9')">Monitor</button>
    <button class="wt-hc-btn wt-hc-topo"    onclick="viewTopology('Abc1...xyz9')">Topology</button>
    <button class="wt-hc-btn wt-hc-dismiss" onclick="dismissHC('Abc1...xyz9')">Dismiss</button>
  </div>
</div>
```

```css
.wt-hc-item {
  padding: 10px 12px;
  border-bottom: 1px solid rgba(255,255,255,.05);
  display: flex;
  gap: 10px;
  align-items: flex-start;
  border-left: 3px solid rgba(249,115,22,.5);
  animation: wt-hc-entry .5s ease-out;
}

@keyframes wt-hc-entry {
  from { background: rgba(249,115,22,.2); border-left-color: #f97316; }
  to   { background: transparent; border-left-color: rgba(249,115,22,.5); }
}

/* Pulse animation for items already in queue */
.wt-hc-item.pulsing {
  animation: wt-hc-pulse 3s ease-in-out infinite;
}
@keyframes wt-hc-pulse {
  0%,100% { border-left-color: rgba(249,115,22,.5); }
  50%      { border-left-color: rgba(249,115,22,1); box-shadow: -2px 0 12px rgba(249,115,22,.3); }
}

.wt-hc-score-badge {
  flex-shrink: 0;
  width: 36px; height: 36px;
  border-radius: 6px;
  background: rgba(249,115,22,.15);
  border: 1px solid rgba(249,115,22,.4);
  color: #f97316;
  font-size: 15px;
  font-weight: 800;
  font-family: 'SF Mono', monospace;
  display: flex;
  align-items: center;
  justify-content: center;
}

.wt-hc-body { flex: 1; min-width: 0; }

.wt-hc-addr-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }

.wt-copy-btn {
  background: none; border: none; color: #6e7681; cursor: pointer;
  font-size: 11px; padding: 1px 3px;
}
.wt-copy-btn:hover { color: #22d3ee; }

.wt-hc-meta {
  font-size: 10px; color: #8b949e; margin-bottom: 4px;
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}
.wt-hc-amount { font-family: 'SF Mono', monospace; color: #f59e0b; }

.wt-fingerprint-match {
  font-size: 9px; font-weight: 700; padding: 1px 5px;
  border-radius: 2px;
}
.fp-yes { background: rgba(34,197,94,.1); color: #22c55e; }
.fp-no  { background: rgba(239,68,68,.1); color: #ef4444; }

/* Urgency bar — fills right to left as time passes (urgency grows) */
.wt-urgency-bar {
  display: inline-block;
  width: 60px; height: 3px;
  background: rgba(255,255,255,.06);
  border-radius: 2px;
  overflow: hidden;
}
.wt-urgency-fill {
  height: 100%;
  background: linear-gradient(90deg, #f59e0b, #ef4444);
  border-radius: 2px;
  transition: width 1s linear;
}
/* Urgency at 100% = 2 hours since funding, blink at that point */
.wt-urgency-fill.maxed { animation: wt-sev-blink .5s step-end infinite; }

.wt-hc-actions {
  display: flex; flex-direction: column; gap: 3px;
  flex-shrink: 0;
}
.wt-hc-btn {
  font-size: 9px; padding: 3px 8px; border-radius: 3px; cursor: pointer;
  border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.04);
  color: #8b949e; white-space: nowrap;
}
.wt-hc-monitor:hover { border-color: rgba(34,211,238,.4); color: #22d3ee; }
.wt-hc-topo:hover    { border-color: rgba(139,92,246,.4); color: #8b5cf6; }
.wt-hc-dismiss:hover { border-color: rgba(239,68,68,.3);  color: #ef4444; }
```

The urgency fill is updated every 30s. Urgency = `(now - funded_ts) / 7200` capped at 1.0.

---

## 11. COLOR SYSTEM

### CSS Custom Properties (complete set)

```css
:root {
  /* ── Backgrounds ──────────────────────────────── */
  --bg-primary:   #0b0f14;   /* page background */
  --bg-secondary: #0a0e13;   /* panel backgrounds */
  --bg-tertiary:  #0f141b;   /* alternate panels */
  --bg-card:      #12171f;   /* cards within panels */
  --bg-card-alt:  #161b22;   /* raised card variant */
  --bg-hover:     rgba(255,255,255,.035);
  --bg-selected:  rgba(34,211,238,.06);
  --bg-deep:      #080b10;   /* deepest background, graph canvas */

  /* ── Borders ──────────────────────────────────── */
  --border:       rgba(255,255,255,.06);
  --border-mid:   rgba(255,255,255,.1);
  --border-strong:rgba(255,255,255,.16);

  /* ── Text ─────────────────────────────────────── */
  --text-primary:  #e6edf3;
  --text-secondary:#8b949e;
  --text-muted:    #6e7681;
  --text-dim:      #484f58;
  --text-code:     #a5d6ff;    /* inline code / addresses */

  /* ── Entity Colors ────────────────────────────── */
  --clr-treasury: #eab308;    /* gold */
  --clr-subprov:  #f97316;    /* orange */
  --clr-relay:    #f59e0b;    /* amber */
  --clr-creator:  #22d3ee;    /* cyan */
  --clr-trader:   #3b82f6;    /* blue */
  --clr-token:    #22c55e;    /* green */
  --clr-sweep:    #ef4444;    /* red */
  --clr-pamm:     #8b5cf6;    /* purple */

  /* ── Alert Severity ──────────────────────────── */
  --sev-info:     #6e7681;
  --sev-warning:  #f59e0b;
  --sev-alert:    #f97316;
  --sev-critical: #ef4444;

  /* ── Lifecycle States ────────────────────────── */
  --state-funded:          #484f58;
  --state-candidate:       #22d3ee;
  --state-high-confidence: #f97316;
  --state-launched:        #22c55e;
  --state-abandoned:       #6b7280;
  --state-active:          #3b82f6;
  --state-swept:           #ef4444;
  --state-reloaded:        #f59e0b;
  --state-dormant:         #484f58;

  /* ── Campaign Lifecycle ──────────────────────── */
  --phase-provisioning:    #484f58;
  --phase-creator-live:    #22d3ee;
  --phase-traders-deployed:#3b82f6;
  --phase-amm-active:      #8b5cf6;
  --phase-sweeping:        #ef4444;
  --phase-complete:        #22c55e;

  /* ── Score Bars ──────────────────────────────── */
  --clr-score-low:  #6b7280;   /* <60 */
  --clr-score-cand: #f59e0b;   /* 60-84 */
  --clr-score-hc:   #f97316;   /* 85+ */

  /* ── Graph ───────────────────────────────────── */
  --graph-bg:       #080b10;
  --graph-grid:     rgba(255,255,255,.03);
  --graph-edge-fund:#ffffff;
  --graph-edge-swap:#3b82f6;
  --graph-edge-sweep:#ef4444;
  --graph-edge-create:#22c55e;
  --graph-particle-fund:#ffffff;
  --graph-particle-sweep:#ef4444;
}
```

---

## 12. TYPOGRAPHY SYSTEM

### Font Stack

```css
:root {
  --font-ui:   -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'SF Mono', 'Fira Code', 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace;
}

body { font-family: var(--font-ui); }

/* Address / code elements always use mono */
.wt-addr, .wt-entity-chip, .wt-cc-addr,
.wt-score-inline, [class*="wt-hc-amount"],
code, .mono { font-family: var(--font-mono); }
```

### Size Scale

```css
:root {
  --fs-heading:  16px;   /* panel section headings */
  --fs-stat:     22px;   /* status bar stat values */
  --fs-stat-lg:  28px;   /* HC score badge */
  --fs-body:     12px;   /* default body text */
  --fs-label:    11px;   /* card labels, table cells */
  --fs-badge:    9px;    /* event badges, state pills */
  --fs-address:  10px;   /* truncated wallet addresses */
  --fs-meta:     10px;   /* timestamps, secondary meta */
  --fs-code:     11px;   /* inline values, amounts */
  --fs-panel-title: 10px; /* panel header titles */
}
```

### Letter Spacing

Panel titles, badge labels, and stat labels use `letter-spacing: .08em` to `letter-spacing: .12em`. Body text is `letter-spacing: normal`. Address chips use `letter-spacing: .02em` to aid readability of hex strings.

```css
.wt-panel-title       { letter-spacing: .1em; }
.wt-evt-badge         { letter-spacing: .07em; }
.wt-sb-label          { letter-spacing: .06em; }
.wt-entity-chip       { letter-spacing: .02em; }
.wt-cc-state-badge    { letter-spacing: .07em; }
```

---

## 13. ANIMATION CONCEPTS

### Complete Keyframe Library

```css
/* 1. Event feed entry — slide in from left with flash */
@keyframes wt-feed-enter {
  0%   { opacity: 0; transform: translateX(-8px); background: rgba(34,211,238,.1); }
  40%  { opacity: 1; transform: translateX(0);    background: rgba(34,211,238,.05); }
  100% { background: transparent; }
}

/* 2. Critical feed entry — orange flash */
@keyframes wt-feed-enter-critical {
  0%   { opacity: 0; transform: translateX(-8px); background: rgba(249,115,22,.25); }
  40%  { background: rgba(249,115,22,.12); }
  100% { opacity: 1; transform: translateX(0); background: transparent; }
}

/* 3. HC alert pulse — border glow on right panel item */
@keyframes wt-hc-pulse {
  0%,100% { border-left-color: rgba(249,115,22,.5);
            box-shadow: none; }
  50%     { border-left-color: rgba(249,115,22,1);
            box-shadow: -3px 0 16px rgba(249,115,22,.35); }
}

/* 4. Status bar throb — HC creator count */
@keyframes wt-hc-throb {
  0%,100% { text-shadow: 0 0 8px rgba(249,115,22,.4); }
  50%     { text-shadow: 0 0 24px rgba(249,115,22,.95); }
}

/* 5. Graph node activation — scale + glow */
@keyframes wt-node-activate {
  0%   { r: 12px; filter: none; }
  30%  { r: 18px; filter: drop-shadow(0 0 12px rgba(34,211,238,.8)); }
  100% { r: 14px; filter: drop-shadow(0 0 6px rgba(34,211,238,.4)); }
}
/* Applied in D3: node.transition().duration(600).attr('r', ...) with filter change */

/* 6. Sweep epoch flash — red pulse across swarm panel */
@keyframes wt-sweep-flash {
  0%   { background: rgba(239,68,68,.25); border-color: rgba(239,68,68,.6); }
  50%  { background: rgba(239,68,68,.1); }
  100% { background: transparent; border-color: rgba(255,255,255,.07); }
}

/* 7. Campaign state transition — color shift */
/* Handled via CSS transition on badge background/color, 600ms ease */

/* 8. Status bar count update — number roll */
/* JS-based: CountUp.js-style or custom requestAnimationFrame interpolation */

/* 9. Live dot pulse */
@keyframes wt-dot-pulse {
  0%,100% { opacity: 1; transform: scale(1); }
  50%     { opacity: .5; transform: scale(0.8); }
}

/* 10. Badge pulse for urgent badges */
@keyframes wt-badge-pulse-orange {
  0%,100% { box-shadow: 0 0 0 rgba(249,115,22,0); }
  50%     { box-shadow: 0 0 8px rgba(249,115,22,.5); }
}

/* 11. Swarm ring glow */
@keyframes wt-ring-glow {
  0%,100% { filter: drop-shadow(0 0 3px rgba(59,130,246,.4)); }
  50%     { filter: drop-shadow(0 0 10px rgba(59,130,246,.8)); }
}

/* 12. Severity blink */
@keyframes wt-sev-blink { 50% { opacity: 0; } }

/* 13. Refresh ring */
@keyframes wt-ring-spin { to { transform: rotate(360deg); } }
```

### JS Number Roll

```javascript
function animateCount(el, from, to, duration = 800) {
  const start = performance.now();
  function frame(now) {
    const t = Math.min((now - start) / duration, 1);
    const ease = t < .5 ? 2*t*t : -1+(4-2*t)*t;
    el.textContent = Math.round(from + (to - from) * ease);
    if (t < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
```

---

## 14. PANEL LAYOUT SPEC

### Left Feed Panel

```css
#wt-feed-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
}

#wt-feed-filter-bar {
  flex-shrink: 0;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  display: flex; gap: 4px; flex-wrap: wrap;
}
/* Type filter toggle buttons */
.wt-type-filter {
  font-size: 9px; padding: 2px 7px; border-radius: 12px;
  border: 1px solid rgba(255,255,255,.08);
  background: none; color: #6e7681; cursor: pointer;
}
.wt-type-filter.active { background: rgba(255,255,255,.07); color: #e6edf3; }

#wt-feed-list {
  flex: 1;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,.08) transparent;
}
```

Empty state:
```html
<div class="wt-empty-state">
  <div class="wt-empty-icon">◈</div>
  <div class="wt-empty-msg">No events yet</div>
  <div class="wt-empty-sub">Waiting for activity…</div>
</div>
```

```css
.wt-empty-state {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-dim);
}
.wt-empty-icon { font-size: 24px; margin-bottom: 8px; opacity: .3; }
.wt-empty-msg  { font-size: 12px; color: var(--text-muted); }
.wt-empty-sub  { font-size: 10px; color: var(--text-dim); margin-top: 4px; }
```

Loading state: skeleton rows with CSS shimmer animation:
```css
.wt-skeleton {
  height: 60px; margin: 4px 12px;
  background: linear-gradient(90deg, rgba(255,255,255,.04) 25%, rgba(255,255,255,.07) 50%, rgba(255,255,255,.04) 75%);
  background-size: 200% 100%;
  animation: wt-shimmer 1.4s infinite;
  border-radius: 4px;
}
@keyframes wt-shimmer { to { background-position: -200% 0; } }
```

Error state:
```html
<div class="wt-error-state">
  <span class="wt-error-icon">⚠</span>
  <span class="wt-error-msg">Feed unavailable — retrying in <span id="feed-retry-count">30</span>s</span>
</div>
```

### Center Panel

```css
#wt-center {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

#wt-creator-strip   { flex-shrink: 0; height: 140px; }
#wt-graph-controls  { flex-shrink: 0; height: 36px; padding: 0 12px;
  display: flex; align-items: center; gap: 8px;
  border-bottom: 1px solid var(--border); }
#wt-graph-container { flex: 1; overflow: hidden; background: var(--graph-bg); }
```

### Right Queue Panel

```css
#wt-queue-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100%;
}

#wt-hc-queue        { flex-shrink: 0; max-height: 45%; border-bottom: 1px solid var(--border); }
#wt-campaign-list   { flex: 1; overflow-y: auto; }
#wt-sweep-timeline  { flex-shrink: 0; height: 80px; border-top: 1px solid var(--border); }
```

Within `#wt-hc-queue`, the list is scrollable when items exceed max-height. The queue header shows a count badge:

```css
.wt-hc-count {
  background: rgba(249,115,22,.2);
  color: #f97316;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 10px;
  font-family: var(--font-mono);
}
.wt-hc-count:empty, .wt-hc-count[data-count="0"] { display: none; }
```

---

## 15. API ENDPOINT MAPPING

### Existing Endpoints and Their Panel Assignments

| Endpoint | Panel | Interval | Notes |
|---|---|---|---|
| `/api/watchtower/live-metrics` | Status bar | 30s | powers all 10 stat chips |
| `/api/watchtower/launch-candidates` | Creator strip + HC queue | 20s | split by score ≥85 for HC |
| `/api/watchtower/operational-status` | Graph data seed | 60s | seeds node list for D3 |
| `/api/network-diagram/watchtower/live` | Topology graph edges | 60s initial | adds new edges since last poll |
| `/api/watchtower/sweep-events` | Sweep timeline | 30s | epoch track rendering |
| `/api/watchtower/operator-stats` | Status bar (trader count) | 60s | provides state breakdown |
| `/api/watchtower/creators` | Creator strip | 20s | enriched creator list |
| `/api/watchtower/staged-wallets` | (background) | 60s | feeds graph node states |
| `/api/network-diagram/watchtower/signals` | Event feed (fallback) | 30s | until new events-feed exists |

### New Endpoints Required

**`/api/watchtower/events-feed`** — unified semantic alert stream.

```python
@app.route('/api/watchtower/events-feed')
def api_wt_events_feed():
    since = int(request.args.get('since', int(time.time()) - 3600))
    limit = int(request.args.get('limit', 100))
    # Merge from:
    #   watchtower_events (creator/launch signals)
    #   wt_discovery_log (new candidates, sub-provs)
    #   watchtower_sweep_events (sweep epochs)
    # Normalize each to { id, type, severity, ts, entity, campaign_id, detail, payload }
    # Return sorted desc by ts
```

**`/api/watchtower/campaigns`** — campaign-centric aggregation.

```python
@app.route('/api/watchtower/campaigns')
def api_wt_campaigns():
    # Group sub-provisioners + their creator children + trader wallets + target tokens
    # Compute phase, heat score, SOL deployed/swept
    # Return list of campaign objects
```

**`/api/watchtower/swarms`** — trader swarm summaries.

```python
@app.route('/api/watchtower/swarms')
def api_wt_swarms():
    # Group watchtower_operator_graph child wallets into cohorts
    # by shared sub-provisioner + active token + time window
    # Compute sync score, sweep velocity
```

---

## 16. INTERACTION MODEL

### Click Behaviors

**Click wallet address (any `.wt-entity-chip`):**
```javascript
document.addEventListener('click', e => {
  const chip = e.target.closest('.wt-entity-chip');
  if (!chip) return;
  openEntityDrawer(chip.dataset.address, chip.dataset.type);
});
```

The entity drawer slides in from the right edge, overlapping the queue panel at z-index 200. It's a 360px-wide panel with full entity detail: all linked addresses, lifecycle state history, score breakdown, linked events.

```css
#wt-entity-drawer {
  position: fixed;
  top: 52px; right: -380px; bottom: 0;
  width: 360px;
  background: #12171f;
  border-left: 1px solid rgba(255,255,255,.1);
  z-index: 200;
  transition: right .3s cubic-bezier(.4, 0, .2, 1);
  overflow-y: auto;
  box-shadow: -4px 0 24px rgba(0,0,0,.5);
}
#wt-entity-drawer.open { right: 0; }
```

**Click campaign card → center view shifts to campaign topology:**
```javascript
function focusCampaign(campaignId) {
  // 1. Set campaign filter on graph
  graph.filterByCampaign(campaignId);
  // 2. Highlight campaign card in right panel
  document.querySelectorAll('.wt-campaign-card').forEach(c =>
    c.classList.toggle('selected', c.dataset.campaignId === campaignId));
  // 3. Update graph controls dropdown
  document.getElementById('graph-campaign-filter').value = campaignId;
}
```

**Click event in feed → highlight related graph nodes:**
```javascript
function onFeedEventClick(eventEl) {
  const entityAddr = eventEl.dataset.entityAddress;
  const campaignId = eventEl.dataset.campaign;
  // Highlight in graph
  graph.highlightNeighborhood(entityAddr);
  if (campaignId) graph.dimOutsideCampaign(campaignId);
}
```

**Hover node in graph → tooltip:**

```javascript
showTooltip(event, d) {
  const tip = document.getElementById('wt-graph-tooltip');
  tip.innerHTML = `
    <div class="tip-type">${d.type}</div>
    <div class="tip-addr">${d.id.slice(0,8)}…${d.id.slice(-6)}</div>
    ${d.confidence ? `<div class="tip-conf">Confidence: ${(d.confidence*100).toFixed(0)}%</div>` : ''}
    ${d.state ? `<div class="tip-state">${d.state}</div>` : ''}
    <div class="tip-hint">Click to expand</div>
  `;
  tip.style.display = 'block';
  tip.style.left = (event.pageX + 12) + 'px';
  tip.style.top  = (event.pageY - 28) + 'px';
}
```

```css
#wt-graph-tooltip {
  position: fixed;
  display: none;
  z-index: 300;
  background: #161b22;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 11px;
  line-height: 1.6;
  pointer-events: none;
  max-width: 220px;
  box-shadow: 0 4px 16px rgba(0,0,0,.5);
}
.tip-type { font-size: 9px; font-weight: 700; letter-spacing: .08em;
            text-transform: uppercase; color: var(--text-muted); }
.tip-addr { font-family: var(--font-mono); font-size: 11px; color: var(--clr-creator); }
.tip-hint { font-size: 9px; color: var(--text-dim); margin-top: 4px; }
```

**Click sub-provisioner node → expand all children:**
```javascript
// On click of SUB_PROV node in D3:
if (d.type === 'SUB_PROV') {
  fetch(`/api/watchtower/operator/${d.id}`)
    .then(r => r.json())
    .then(data => {
      // Add child nodes that aren't yet in graph
      const newNodes = data.children.filter(c => !graph.nodeIndex[c.address]);
      newNodes.forEach(c => graph.nodes.push({ id: c.address, type: c.role, ... }));
      data.children.forEach(c => graph.edges.push({
        id: `${d.id}-${c.address}`, source: d.id, target: c.address, type: 'provisioned_by'
      }));
      graph.render();
    });
}
```

---

## 17. MOBILE CONSIDERATIONS

### Breakpoint Behavior

At `< 768px`: three-column layout collapses to single column, stacked.

Priority order for small screens:
1. Status bar (condensed to 2 rows)
2. HC queue (full width, first)
3. Event feed (full width)
4. Creator strip (horizontal scroll, visible)
5. Graph (hidden by default, toggle button to show overlay)
6. Campaign cards (collapsed accordion)
7. Sweep timeline (hidden, accessible via tab)

```css
@media (max-width: 767px) {
  #wt-main {
    grid-template-columns: 1fr;
    grid-template-rows: auto;
    height: auto;
    overflow: visible;
  }
  #wt-feed-panel  { order: 2; height: 400px; }
  #wt-center      { order: 3; height: 420px; }
  #wt-queue-panel { order: 1; }

  #wt-graph-container { display: none; }
  #wt-graph-toggle {
    display: block;
    /* button to show graph as a modal overlay on mobile */
  }
}

@media (min-width: 768px) and (max-width: 1100px) {
  #wt-main {
    grid-template-columns: 240px 1fr;
    grid-template-rows: 1fr auto;
  }
  #wt-feed-panel  { grid-column: 1; grid-row: 1; }
  #wt-center      { grid-column: 2; grid-row: 1; }
  #wt-queue-panel { grid-column: 1 / span 2; grid-row: 2; height: 200px;
    display: flex; flex-direction: row; gap: 8px; }
}
```

### Touch Interactions for Graph

```javascript
// D3 zoom with touch support
const zoom = d3.zoom()
  .scaleExtent([.3, 4])
  .on('zoom', event => {
    graph.svg.select('g.wt-zoom-group')
      .attr('transform', event.transform);
  });
graph.svg.call(zoom);

// Long-press on node = expand (replaces right-click)
let longPressTimer;
node.on('touchstart', (event, d) => {
  longPressTimer = setTimeout(() => {
    event.preventDefault();
    openEntityDrawer(d);
  }, 600);
}).on('touchend', () => clearTimeout(longPressTimer));
```

---

## 18. HTML TEMPLATE STRUCTURE

Full skeleton for `watchtower_dashboard.html`:

```html
{% extends "base_shell.html" %}
{% block title %}WATCHTOWER — Live Intelligence Dashboard{% endblock %}

{% block extra_styles %}
/* CSS defined here — see sections 11, 12, 13, 14 */
{% endblock %}

{% block content %}

<!-- ── STATUS BAR ──────────────────────────────────── -->
<header id="wt-status-bar">
  <div class="wt-sb-left">
    <span class="wt-sb-brand">WATCHTOWER</span>
    <span class="wt-sb-heat" id="sb-heat" data-level="cold">COLD</span>
  </div>
  <div class="wt-sb-stats" id="wt-sb-stats">
    <div class="wt-sb-stat" id="sb-creator-cands">
      <span class="wt-sb-glyph" style="color:var(--clr-creator)">◈</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-creator-cands-val">—</span>
        <span class="wt-sb-delta" id="sb-creator-cands-delta"></span>
      </div>
      <span class="wt-sb-label">Candidates</span>
    </div>
    <div class="wt-sb-stat" id="sb-hc-creators">
      <span class="wt-sb-glyph" style="color:var(--clr-subprov)">⬡</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-hc-val">—</span>
      </div>
      <span class="wt-sb-label">High Conf</span>
    </div>
    <div class="wt-sb-stat" id="sb-trader-wallets">
      <span class="wt-sb-glyph" style="color:var(--clr-trader)">⬢</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-trader-val">—</span>
        <span class="wt-sb-sub" id="sb-swarm-count"></span>
      </div>
      <span class="wt-sb-label">Active Traders</span>
    </div>
    <div class="wt-sb-stat" id="sb-pamm">
      <span class="wt-sb-glyph" style="color:var(--clr-pamm)">◉</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-pamm-val">—</span>
      </div>
      <span class="wt-sb-label">pAMM Campaigns</span>
    </div>
    <div class="wt-sb-stat" id="sb-sweeps">
      <span class="wt-sb-glyph" style="color:var(--clr-sweep)">↙</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-sweeps-val">—</span>
        <span class="wt-sb-sub" id="sb-sweeps-sol"></span>
      </div>
      <span class="wt-sb-label">Sweeps 24h</span>
    </div>
    <div class="wt-sb-stat" id="sb-reloads">
      <span class="wt-sb-glyph" style="color:var(--clr-relay)">↺</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-reloads-val">—</span>
      </div>
      <span class="wt-sb-label">Reloads 24h</span>
    </div>
    <div class="wt-sb-stat" id="sb-launches">
      <span class="wt-sb-glyph" style="color:var(--clr-token)">⬆</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-launches-val">—</span>
      </div>
      <span class="wt-sb-label">Launches 24h</span>
    </div>
    <div class="wt-sb-stat" id="sb-subprov">
      <span class="wt-sb-glyph" style="color:var(--clr-subprov)">◆</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-subprov-val">—</span>
        <span class="wt-sb-delta" id="sb-subprov-delta"></span>
      </div>
      <span class="wt-sb-label">New Sub-Provs</span>
    </div>
    <div class="wt-sb-stat" id="sb-treasury">
      <span class="wt-sb-glyph" style="color:var(--clr-treasury)">⬡</span>
      <div class="wt-sb-val-row">
        <span class="wt-sb-val" id="sb-treasury-val">—</span>
        <span class="wt-sb-sub">SOL</span>
      </div>
      <span class="wt-sb-label">Treasury 24h</span>
    </div>
  </div>
  <div class="wt-sb-right">
    <span class="wt-sb-ts" id="sb-ts"></span>
    <span class="wt-sb-dot" id="sb-live-dot"></span>
  </div>
</header>

<!-- ── MAIN THREE-COLUMN LAYOUT ───────────────────── -->
<main id="wt-main">

  <!-- LEFT: Event Feed -->
  <aside id="wt-feed-panel">
    <div class="wt-panel-hdr">
      <span class="wt-panel-title">LIVE EVENTS</span>
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="wt-refresh-ring" id="feed-refresh-ring"></span>
      </div>
    </div>
    <div id="wt-feed-filter-bar">
      <!-- type filter buttons populated by JS -->
    </div>
    <div id="wt-feed-list">
      <div class="wt-skeleton"></div>
      <div class="wt-skeleton" style="opacity:.6"></div>
      <div class="wt-skeleton" style="opacity:.3"></div>
    </div>
  </aside>

  <!-- CENTER: Creator Strip + Graph -->
  <section id="wt-center">

    <!-- Creator candidate horizontal strip -->
    <div id="wt-creator-strip">
      <div class="wt-panel-hdr">
        <span class="wt-panel-title">CREATOR CANDIDATES</span>
        <span class="wt-strip-count" id="cand-count">loading…</span>
      </div>
      <div class="wt-creator-scroll" id="creator-scroll">
        <!-- creator cards populated by JS -->
      </div>
    </div>

    <!-- Graph toolbar -->
    <div id="wt-graph-controls">
      <button class="wt-gctrl" onclick="wtGraph.resetZoom()" title="Reset zoom">⊞</button>
      <button class="wt-gctrl wt-gctrl-toggle" id="particle-toggle"
              onclick="wtGraph.toggleParticles()" title="Toggle flow particles">≈</button>
      <select class="wt-gctrl-select" id="graph-campaign-filter"
              onchange="wtGraph.filterByCampaign(this.value)">
        <option value="">All campaigns</option>
      </select>
      <label class="wt-gctrl-label" style="display:flex;align-items:center;gap:6px;">
        <input type="range" id="graph-time-filter" min="0" max="100" value="100"
               oninput="wtGraph.filterByTime(this.value)" style="width:80px">
        <span id="graph-time-label" style="font-size:10px;color:#6e7681">All</span>
      </label>
      <span class="wt-gctrl-sep" style="flex:1"></span>
      <div class="wt-graph-legend-mini" id="graph-legend">
        <!-- legend items -->
      </div>
    </div>

    <!-- D3 graph canvas -->
    <div id="wt-graph-container">
      <!-- SVG injected by D3 -->
    </div>

    <!-- Tooltip (shared) -->
    <div id="wt-graph-tooltip"></div>

  </section>

  <!-- RIGHT: HC Queue + Campaigns + Sweep Timeline -->
  <aside id="wt-queue-panel">

    <!-- High Confidence Creator Queue -->
    <div id="wt-hc-queue">
      <div class="wt-panel-hdr">
        <span class="wt-panel-title">HIGH CONFIDENCE</span>
        <span class="wt-hc-count" id="hc-count">0</span>
      </div>
      <div id="hc-queue-list" style="overflow-y:auto;max-height:calc(45vh - 36px)">
        <!-- HC items populated by JS -->
      </div>
      <div class="wt-empty-state" id="hc-empty">
        <div class="wt-empty-icon" style="color:var(--clr-subprov)">⬡</div>
        <div class="wt-empty-msg">No high-confidence creators</div>
      </div>
    </div>

    <!-- Active Campaigns -->
    <div id="wt-campaign-list" style="overflow-y:auto;flex:1;border-top:1px solid var(--border);">
      <div class="wt-panel-hdr">
        <span class="wt-panel-title">CAMPAIGNS</span>
        <span class="wt-refresh-ring" id="camp-refresh-ring"></span>
      </div>
      <div id="campaign-cards">
        <!-- campaign cards populated by JS -->
      </div>
    </div>

    <!-- Sweep Timeline -->
    <div id="wt-sweep-timeline">
      <div class="wt-panel-hdr">
        <span class="wt-panel-title">SWEEP EPOCHS 24H</span>
        <span id="sweep-24h-total" style="font-size:10px;font-family:monospace;color:var(--clr-sweep)">0 SOL</span>
      </div>
      <div class="wt-epoch-track" id="epoch-track"></div>
      <div class="wt-epoch-axis">
        <span>-24h</span><span>-18h</span><span>-12h</span><span>-6h</span><span>now</span>
      </div>
    </div>

  </aside>

</main>

<!-- Entity detail drawer (slides in from right) -->
<div id="wt-entity-drawer">
  <div class="wt-drawer-hdr">
    <span class="wt-drawer-title" id="drawer-title">Entity Detail</span>
    <button onclick="closeEntityDrawer()" class="wt-drawer-close">✕</button>
  </div>
  <div id="wt-drawer-body">
    <!-- populated by openEntityDrawer() -->
  </div>
</div>
<div id="wt-drawer-overlay" onclick="closeEntityDrawer()"
     style="display:none;position:fixed;inset:0;z-index:199;background:rgba(0,0,0,.4)"></div>

{% endblock %}

{% block extra_scripts %}
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
  // All dashboard JS inline — see sections 4, 6, 10, 16
</script>
{% endblock %}
```

---

## 19. CSS ARCHITECTURE

### CSS Variable Naming Convention

- `--bg-*` — backgrounds, ordered primary (darkest) to card (lightest)
- `--text-*` — text, ordered primary (brightest) to dim (dimmest)
- `--border`, `--border-mid`, `--border-strong` — three border intensity levels
- `--clr-{entity}` — entity type colors (treasury, subprov, creator, trader, etc.)
- `--sev-{level}` — alert severity colors
- `--state-{lifecycle}` — lifecycle state colors
- `--phase-{name}` — campaign phase colors
- `--clr-score-{level}` — score bar colors
- `--graph-*` — graph-specific colors
- `--font-ui`, `--font-mono` — font stacks
- `--fs-*` — font size scale
- `--wt-*` — component-specific overrides (prefix for WATCHTOWER-specific variables)

### Card Component Pattern

```css
.wt-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}
.wt-card-hdr {
  height: 36px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
}
.wt-card-body { padding: 0; }
.wt-card-body.padded { padding: 10px 12px; }
```

### Badge Component Pattern

All badges follow: background tint + colored text + optional border + uppercase + tight letter-spacing.

```css
/* Base badge */
[class*="wt-badge"], [class*="ev-"], [class*="phase-"],
[class*="state-"], [class*="disc-"] {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .07em;
  text-transform: uppercase;
  white-space: nowrap;
  line-height: 1.4;
}
```

### Score Bar Component

```css
.wt-score-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wt-score-bar-track {
  flex: 1;
  height: 5px;
  background: rgba(255,255,255,.06);
  border-radius: 3px;
  overflow: hidden;
}
.wt-score-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width .5s ease-out;
}
.wt-score-bar-val {
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  width: 28px;
  text-align: right;
}
```

Score fill color is set inline via JS using `scoreColor(score)`.

### State Badge Component

```css
/* Usage: <span class="wt-state-badge" data-state="HIGH_CONFIDENCE"> */
.wt-state-badge[data-state="FUNDED"]           { background: rgba(72,79,88,.15);  color: var(--state-funded); }
.wt-state-badge[data-state="CREATOR_CANDIDATE"]{ background: rgba(34,211,238,.1); color: var(--state-candidate); }
.wt-state-badge[data-state="HIGH_CONFIDENCE"]  { background: rgba(249,115,22,.15);color: var(--state-high-confidence); }
.wt-state-badge[data-state="LAUNCHED"]         { background: rgba(34,197,94,.1);  color: var(--state-launched); }
.wt-state-badge[data-state="ABANDONED"]        { background: rgba(107,114,128,.1);color: var(--state-abandoned); }
.wt-state-badge[data-state="ACTIVE"]           { background: rgba(59,130,246,.1); color: var(--state-active); }
.wt-state-badge[data-state="SWEPT"]            { background: rgba(239,68,68,.1);  color: var(--state-swept); }
.wt-state-badge[data-state="RELOADED"]         { background: rgba(245,158,11,.12);color: var(--state-reloaded); }
.wt-state-badge[data-state="DORMANT"]          { background: rgba(72,79,88,.1);   color: var(--state-dormant); }
```

---

## 20. FUTURE EXTENSIBILITY

### New Alert Types

The event type system is data-driven. Adding a new type requires:
1. Adding a row to the `EVENT_TYPE_CONFIG` JS object:
```javascript
const EVENT_TYPE_CONFIG = {
  // ... existing types ...
  'NEW_EXCHANGE_DEPOSIT': {
    color: '#a78bfa', bg: 'rgba(167,139,250,.12)',
    border: 'rgba(167,139,250,.25)', glyph: '⬡',
    label: 'EXCHANGE DEP', severity: 'warning'
  }
};
```
2. Adding the badge CSS class `.ev-NEW_EXCHANGE_DEPOSIT` (or making it data-attribute-driven to avoid this).

Recommended refactor: move to data-attribute badge system so zero CSS changes are needed:
```css
.wt-evt-badge[data-type] {
  background: color-mix(in srgb, var(--badge-clr) 12%, transparent);
  color: var(--badge-clr);
  border: 1px solid color-mix(in srgb, var(--badge-clr) 30%, transparent);
}
```
With `style="--badge-clr: #a78bfa"` set by JS from config.

### New Entity Types

Node type rendering in D3 uses the `NODE_COLORS` lookup table. New entity type = new entry in that object. No other changes needed if the pattern is followed.

### Multi-Operation Monitoring

The dashboard is scoped to WATCHTOWER by the `operation` query parameter:

```
/watchtower/dashboard?op=WATCHTOWER   ← default
/watchtower/dashboard?op=HTX          ← future operation
```

All API calls pass `?op=WATCHTOWER` already. The status bar brand label and color theme are switchable via a data attribute on `<body>`:

```css
body[data-op="WATCHTOWER"] { --op-accent: #22d3ee; }
body[data-op="HTX"]        { --op-accent: #a78bfa; }
```

### Historical Replay Mode

Add a replay toolbar that appears when `?mode=replay&from=X&to=Y` is in the URL:

```html
<div id="wt-replay-bar" style="display:none">
  <input type="range" id="replay-scrubber" min="0" max="100" value="0">
  <span id="replay-ts-label"></span>
  <button onclick="replayPlay()">▶ Play</button>
  <button onclick="replayPause()">⏸</button>
  <select id="replay-speed">
    <option value="1">1x</option><option value="5">5x</option><option value="30">30x</option>
  </select>
</div>
```

In replay mode, all API calls include `?before=<ts>` parameter. The graph filters by temporal slider. The event feed rebuilds from static snapshot.

### Export / Reporting

Add export button to campaign cards and HC queue:

```javascript
function exportCampaign(campaignId) {
  fetch(`/api/watchtower/campaigns/${campaignId}/export`)
    .then(r => r.blob())
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `wt-campaign-${campaignId}.json`;
      a.click();
    });
}
```

New endpoint `/api/watchtower/campaigns/<id>/export` returns a structured JSON object suitable for loading into Chainalysis Reactor or similar external tooling.

---

### Critical Files for Implementation

- `/Users/kevinkeaveney/Dev/claude/flex/templates/watchtower_operational_intelligence.html` — current template to be superseded; the new `watchtower_dashboard.html` should follow its CSS variable conventions and `{% extends "base_shell.html" %}` pattern
- `/Users/kevinkeaveney/Dev/claude/flex/templates/base_shell.html` — the shell layout all templates extend; the sticky status bar and the `height: calc(100vh - 52px)` main grid require that the `.container` padding does not interfere
- `/Users/kevinkeaveney/Dev/claude/flex/src/core/main.py` — all new API endpoints (`/api/watchtower/events-feed`, `/api/watchtower/campaigns`, `/api/watchtower/swarms`) are added here, following the existing route pattern at line 25248 onward
- `/Users/kevinkeaveney/Dev/claude/flex/templates/network_diagram_watchtower.html` — source of the existing D3 graph implementation (activation alert, particle concepts, node/edge rendering) to be refactored into the embedded graph panel
- `/Users/kevinkeaveney/Dev/claude/flex/templates/watchtower_intelligence.html` — existing state pill, tab, and table patterns that should be preserved and referenced for component consistency in the new dashboard