# WATCHTOWER Intelligence Page — Reference

**URL:** `http://localhost:5002/watchtower/intelligence`
**Template:** `templates/watchtower_operational_intelligence.html`
**Refresh:** auto every 15 seconds

---

## Purpose

The pre-launch intelligence console. Everything about the WATCHTOWER operator network in one view:
who the treasuries are, which subprovs are active, whether the real-time cascade is running, and
whether detections would be actionable if they fired. It is an operational and investigative tool,
not a trading tool.

---

## Sections (top to bottom)

### 1. Hero Status Cards
Quick summary of the pipeline state: ARMED count, treasuries monitored vs blind, template events
in 24h, pre-launch creators detected, creators in countdown window, average lead time, webhook hits,
listener status.

### 2. Active WebSocket Cascade
Real-time status of the `ws_cascade` daemon. Shows:
- WS connection health (LIVE / STALE / DOWN) + heartbeat age
- Cards: active SUB_PROVs, candidate watches, launches total, last wrap-close, last CREATE
- Table of active cascade sessions: which subprov is being watched, its funding treasury, funding
  SOL, how many candidate wallets are open, session TTL, and the specific candidate wallets it is
  monitoring (i.e. every `closeAccount.destination` from the subprov's wrap-closes, waiting for one
  to fire a CREATE)
- Banner showing the most recent confirmed WATCHTOWER launch (mint, creator, treasury, birth→launch
  gap, INSTANT/STAGED mode)

### 3. Launch Audit
Answers: **when we detect a launch, how much upside is left?** Headline KPI = `peak_mc / mc_at_detection`.
- Cards: live catch count, fixture count, median detection latency, MC at detection, peak MC,
  median actionable multiple, % >2x, % dumped before migration
- Actionable-multiple bucket breakdown: <1.5x / 1.5-2x / 2-5x / 5-10x / 10x+
- Table of individual audited launches: mint, creator, funding SOL (subprov load / wrap-close seed),
  detection latency (ms), position (#N buyer), MC at detection, peak MC, actionable multiple,
  time to peak, outcome (MIGRATED / DUMPED)
- Fixtures (historical backfill) shown dimmed with a FIXTURE tag; live catches shown at full opacity

### 4. Confirmed Treasury Bank
The authoritative set of wallets confirmed as WATCHTOWER treasuries. Shows:
- Cards: total confirmed, webhooked vs total, last hit, last fan-out, last strict candidate, last
  fired token
- Table: each treasury, total out SOL, recipient count, webhooked status, last hit, confidence level

### 5. Apex Funders
Who funds the treasuries (the layer above). Classifies each as:
- `TREASURY MESH` — a known treasury funding another (network growth / rotation)
- `HUB` — funds multiple treasuries (a capital hub above the treasury layer)
- `EXTERNAL` — genuine outside capital (expansion candidate)
- `BUY_SWARM` — the treasury also funds a trading/swap operation (not expansion)
- `RECYCLING` / `SWEEP` — capital coming back up from lower tiers

Zero RPC — derived entirely from the webhook hit log.

### 6. Sub-Provisioners
The wallets that do the actual wrap-close creator provisioning. Two categories:
- `✓ known treasury` — the wallet that funded this subprov has been confirmed as a treasury
- `⚠️ UNKNOWN — investigate` — funder not yet confirmed; each unknown row is a lead to a new treasury

Actions per row: `+ set funder` (prompts for the treasury address, verifies on-chain via 1 RPC,
auto-confirms + webhooks); `✕ remove` (clears a wrong attribution).

Distribution nodes (subprovs that also fund other subprovs) shown with a 🪢 badge.

### 7. Vanity Family
Wallets sharing a deliberate on-chain vanity prefix with known WATCHTOWER infra (e.g. the `44or`
family: treasury + signallers all start with `44or`). A new wallet sharing the prefix is
same-operator evidence (not a confirmed role). Shows configured families and any newly observed
prefix matches.

### 8. Potential Treasuries
Discovery engine output — wallets that look like treasuries but haven't been confirmed yet.
Three signals are required (transfer-purity, capital-scale, micro-pings). Categories:
- `✓ APPROVE NOW` — 3-of-3 signals, repeat occurrences, high peak MC
- `👁 WATCH` — high SOL, limited evidence
- `✗ REJECT LIKELY` — mesh-only, 0 peak MC, weak signals

**Recovery-safe approval mode:** approving adds the wallet to the confirmed treasury registry
ONLY. It does NOT enrol webhooks, rewrite operations, or activate any pipeline machinery.
Webhook enrolment is a separate button, disabled until the wallet is approved.

Includes an approval audit trail of previously approved/rejected candidates.

### 9. Likely To Launch Next
Template-funded creators currently in the ~58-min launch window. Countdown colour bands:
- Red `<15m` — imminent
- Orange `15-30m`
- Yellow `30-60m`
- Green `60m+` (recently funded, early in window)

Clicking a creator links to Solscan. Each row shows the operation it belongs to, treasury, template
type, when it was funded, minutes since funding, expected launch time, and webhook status.

### 10. Anchor Health
Are the treasury/collector webhooks actually delivering events? Shows each webhooked anchor, whether
it's live or silent, last hit, and hits in the last hour. A silent anchor = real-time detection is
blind for that treasury.

### 11. Active Forward Walks
Chain-following sessions currently in flight (treasury trigger → subprov → creator). Shows session
ID, trigger wallet, operation, current wallet being watched, depth, state (WALKING / ARMED /
EXPIRED), and TTL or stop reason.

### 12. Creator Mode Distribution
The STAGED vs INSTANT split for the known creator population:
- Birth→launch gap histogram (0-10s, 10-60s, 1-10m, 10-60m, 60m+)
- Per-treasury mode breakdown — which operators run INSTANT (every creator ~1s, attribution-only)
  vs STAGED (≥60s, catchable with ARMED machinery)
- Dominant mode drives the strategic framing: INSTANT = real-time attribution is the goal,
  STAGED = pre-launch prediction is viable

### 13. ARMED
Creators that have been auto-webhooked on template funding, with a countdown to expected launch.
Overdue arms shown dimmed with an honest "auto-expiring" label. "Recently Fired" section shows
arms that successfully preceded a migration, with the actual lead time delivered.

### 14. Treasury Coverage
Every confirmed treasury, with toggle buttons to enrol/remove from the Helius webhook. Red-shaded
rows = not webhooked (blind). Each row shows launches attributed, TX activity in 24h, last event,
template event count, and current status (MONITORED / BLIND).

### 15. Treasury Attribution Review
Positional resolver flags cases where the assigned treasury may be a relay rather than the true
root. Shows the assigned vs positional-root candidate with confidence and reason. Each wallet has
a `score` button that opens the behavioural role drawer.

### 16. Detection Health + Event Feed
Health cards (listener status, webhook hit rate, RPC follow %). Event feed of pre-launch
detections in time order: CREATOR ARMED, CREATOR FUNDED, PRE-LAUNCH, FORWARD HOP, WALK
STARTED/ENDED, SIGNAL ACTIVATION.

---

## Key interactions

| Action | Effect |
|--------|--------|
| Click any address | Opens Solscan |
| Click an operation UUID | Navigates to `/ops/operation/<uuid>` |
| `score` button (treasury / attribution review) | Opens right-hand role-score drawer |
| `+ set funder` on a subprov | Verifies on-chain + confirms treasury (1 RPC) |
| `✓ Approve` on potential treasury | Adds to registry only, no webhook |
| `🔌 Enroll Webhook` (post-approve) | Activates treasury in live Helius subscription |
| `● ON` / `○ OFF` in Treasury Coverage | Toggles Helius webhook enrolment |

---

## Related docs

- [`WATCHTOWER_DETECTION_VS_PREDICTION.md`](WATCHTOWER_DETECTION_VS_PREDICTION.md) — why creator prediction is impossible; WS cascade as the detection model
- [`SUBPROV_DISTRIBUTION_AND_WS_COVERAGE.md`](SUBPROV_DISTRIBUTION_AND_WS_COVERAGE.md) — subscription gap measurement; distribution tier
- [`WATCHTOWER_TOKEN_LAUNCH_PATTERN.md`](WATCHTOWER_TOKEN_LAUNCH_PATTERN.md) — the wrap-close mechanism this page monitors
