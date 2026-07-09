# FLEX Ecosystem Intelligence OS Blueprint

_Last updated: 2026-05-15_

## North star

FLEX should no longer feel like a suite of analyzers. It should feel like an **Ecosystem Intelligence OS**: a system that lets a user move from a token launch to a capital-allocation judgment with minimal cognitive friction.

The UI should answer:

```text
What is this?
Who is behind it?
Is this ecosystem historically good or bad?
How coordinated is it?
Has this creator/network produced winners before?
Should we care?
Should we allocate capital?
```

It should not force users to understand:
- which analyzer produced a signal
- which table stores it
- which graph algorithm found it

The correct abstraction is:

```text
Token → Creator → Ecosystem → Coordinator/Funder → Portfolio Position
```

Everything else is evidence.

---

# A. Current-page audit

| Page | Primary purpose | Canonical entity | Main data / signals | Overlap | Status |
|---|---|---|---|---|---|
| `/` | live launches | Token | live price, MC, migration | pumpfun, token intel | Canonical |
| `/pumpfun` | source-specific feed | Token | PumpFun tokens | live market | Supporting |
| `/token-intelligence` | token dossier | Token | lifecycle, liquidity, behavior | predictions, risk scoring | Canonical, should absorb more |
| `/predictions` | action queue | Prediction | token prediction scores | risk scoring, approvals | Canonical queue |
| `/approval-queue` | review workflow | Prediction | review state | predictions | Supporting |
| `/network-approval` | review workflow | Ecosystem | network review state | ecosystem explorer | Supporting |
| `/risk-scoring` | model output | Token / Creator / Ecosystem | risk scores | token intel, creator analysis | Supporting |
| `/trading-sim` | strategy cockpit | Portfolio Position | actual simulated trades | profitable lenses | Canonical |
| `/networks` | released networks | Ecosystem | network membership | network intel, ecosystem-networks | Legacy standalone |
| `/network-intelligence` | joined ecosystem context | Ecosystem | network + graph context | networks, clusters, coordinators | Canonical concept, merge target |
| `/clusters` | farm structures | Ecosystem / Cluster | farm clusters | network intel, ecosystem | Supporting lens |
| `/coordinators` | coordinator wallets | Coordinator / Funder | wallet clusters | coordinated-funders, funders | Supporting lens |
| `/coordinated-funders` | coordination evidence | Coordinator / Funder | network coordinators, overlap | coordinators, graph | Supporting lens |
| `/transfer-graph` | raw graph explorer | Evidence | transfer graph | creator/funder/ecosystem graph context | Supporting / diagnostic |
| `/creator-analysis` | creator dossier | Creator | findings, transfers, network context | ecosystem-creators, risk scoring | Canonical |
| `/funder-intelligence` | funder dossier | Coordinator / Funder | lineage, behavior | coordinators, hubs | Canonical |
| `/top-funding-hubs` | upstream concentration | Coordinator / Funder | hubs, upstream links | funder intelligence | Supporting |
| `/ecosystem` | ecosystem home | Ecosystem | historical + sim lenses | networks, clusters | Canonical |
| `/ecosystem-creators` | creator historical lens | Creator | historical outcomes | creator analysis | Supporting lens |
| `/ecosystem-networks` | ecosystem historical lens | Ecosystem | historical outcomes | networks | Supporting lens |
| `/ecosystem-funders` | funder historical lens | Coordinator / Funder | historical expectancy | funder intel | Supporting lens |
| `/ecosystem-clusters` | farm historical lens | Ecosystem / Cluster | cluster outcomes | clusters | Supporting lens |
| `/profitable-creators` | creator sim lens | Creator | sim PnL | ecosystem-creators | Supporting lens |
| `/profitable-networks` | ecosystem sim lens | Ecosystem | sim PnL | ecosystem-networks | Supporting lens |
| `/profitable-funders` | funder sim lens | Coordinator / Funder | sim attribution | ecosystem-funders | Supporting lens |
| `/network-diagram` | investigations | Ecosystem | curated case studies | network intel | Supporting |
| `/spike-analysis` | anomaly analysis | Token | spike behavior | token intel | Supporting |
| `/pump-bots` | bot context | Token / Ecosystem | bot signals | token intel | Supporting |
| `/webhook-monitor` | ingestion health | System | webhook flow | transfers | Diagnostic |
| `/funding-queue` | extraction ops | System | queue state | funding | Diagnostic |
| `/snapshots` | price-history ops | System | snapshot state | token infra | Diagnostic |
| `/vaults` | pool/vault ops | System | discovery/validation | token infra | Diagnostic |
| `/system-health` | service health | System | workers, first snapshot health | all | Diagnostic |
| `/usage` | cost/usage | System | RPC/API usage | system | Diagnostic |
| `/settings` | config | System | settings | system | Diagnostic |

---

# B. Overlap map

## 1. Ecosystem structure overlap

```text
/networks
/network-intelligence
/clusters
/coordinators
/coordinated-funders
/transfer-graph
```

These are not six user concepts. They are six views of one concept:

```text
ECOSYSTEM INTELLIGENCE
```

## 2. Creator overlap

```text
/creator-analysis
/ecosystem-creators
/risk-scoring
```

Should become one canonical Creator page with multiple sections.

## 3. Funder overlap

```text
/funder-intelligence
/coordinators
/coordinated-funders
/top-funding-hubs
/ecosystem-funders
```

Should become one canonical Coordinator / Funder page with structure, lineage, expectancy, and risk.

## 4. Token overlap

```text
/token-intelligence
/predictions
/risk-scoring
/spike-analysis
/pump-bots
```

`Predictions` stays a queue. The Token page becomes the complete dossier.

## 5. Performance overlap

```text
/ecosystem-*
/profitable-*
/trading-sim
```

These are not duplicates. They are adjacent truths:

```text
Historical Performance ≠ Simulation PnL
```

---

# C. Proposed navigation tree

## Primary navigation

```text
1. Live Market
   - Live Launches
   - PumpFun Feed

2. Tokens
   - Token Explorer
   - Predictions
   - Reviews

3. Creators
   - Creator Explorer

4. Ecosystems
   - Ecosystem Explorer
   - Investigations

5. Funding
   - Funder / Coordinator Explorer
   - Hubs

6. Portfolio
   - Simulation Portfolio
   - Strategy Performance

7. System
   - Diagnostics
```

## System / Diagnostics drawer
- Webhook Monitor
- Funding Queue
- Snapshots
- Vaults
- Usage
- System Health
- Raw Transfer Graph
- Settings

## Remove from top-level nav
- Profitable Creators / Networks / Funders
- Clusters
- Coordinators
- Coordinated Funders
- Network Intelligence
- Risk Scoring

These remain as tabs, lenses, or evidence panels.

---

# D. Canonical entity model

## A. Token

### Overview
- identity, symbol, mint
- price, current MC, peak MC
- lifecycle state
- prediction label

### Intelligence summary
- “Why should I care?”
- creator quality
- ecosystem quality
- liquidity state

### Graph / funding context
- creator
- direct funder
- upstream lineage
- coordinator exposure

### Historical performance
- first observed MC
- peak multiple
- current multiple
- migration / behavior history

### Simulation performance
- actual position if traded
- realised / unrealised / equity
- strategy status

### Risk behavior
- liquidity survival
- rug / liquidation signals
- bot support

### Related entities
- creator
- ecosystem
- funder / coordinator

## B. Creator

### Overview
- wallet identity
- launch count
- ecosystem membership
- creator quality + risk

### Intelligence summary
- “This creator repeatedly migrates but has weak liquidity survival.”

### Funding context
- direct funders
- self-funding
- upstream lineage
- coordinator exposure

### Historical performance
- launches
- median peak multiple
- runner counts
- migration / rug / survival
- behavior composition

### Simulation performance
- deployed / realised / unrealised / equity / ROI
- strategy performance

### Graph context
- creator transfers
- farm membership
- coordinated creator edges

### Related entities
- funders
- ecosystems
- similar creators

## C. Ecosystem / Network

### Overview
- name
- size
- quality score
- risk score
- creator count

### Intelligence summary
- “Historically produces runners, but quality is concentrated in a small coordinator-led subgroup.”

### Structure
- coordinators
- shared funding
- creator transfers
- farm structure
- operator concentration

### Historical performance
- token output
- migration / survival
- behavior composition
- runner production
- coverage

### Simulation performance
- actual traded sample
- portfolio PnL
- strategy coverage

### Liquidity / risk
- liquidation cadence
- rug concentration
- high-risk creator mix

### Related entities
- member creators
- dominant funders
- bridge ecosystems

## D. Coordinator / Funder

### Overview
- wallet
- coordinator status
- creator reach
- funding volume

### Intelligence summary
- “This coordinator funds many creators, but most belong to low-quality rotation operators.”

### Funding context
- creators funded
- upstream sources
- shared-funding peers
- overlap density

### Historical performance
- outcomes of creators funded
- migration / runner / rug mix

### Simulation performance
- attribution from actually traded creators

### Risk
- AML / entity labels
- infra / CEX masking
- liquidation association

### Related entities
- creators
- ecosystems
- upstream hubs

## E. Portfolio Position / Simulation

### Overview
- entry
- strategy
- current state

### Intelligence summary
- “Position is profitable, but creator belongs to a weak ecosystem historically.”

### Performance
- deployed / realised / unrealised / equity
- exits by strategy

### Context
- creator
- ecosystem
- funder
- historical expectancy

---

# E. Ecosystem Intelligence area

Replace the fragmented graph/network pages with one area:

## ECOSYSTEM INTELLIGENCE tabs / lenses
- Overview
- Coordinators
- Funding
- Shared Funding
- Creator Transfers
- Farm Structure
- Historical Performance
- Simulation PnL
- Liquidity Behavior
- Risk

### Existing pages absorbed
- `/networks`
- `/network-intelligence`
- `/clusters`
- `/coordinators`
- `/coordinated-funders`
- parts of `/transfer-graph`

The user sees one ecosystem and rotates the lens. The backend can remain as rich as it likes.

---

# F. Unified terminology model

| Backend / legacy term | User-facing term |
|---|---|
| network | Ecosystem |
| wallet cluster | Coordinator Structure |
| funder overlap | Shared Funding |
| coordinated funders | Coordinators / Shared Funding |
| creator c2c edges | Creator Transfers |
| farm clusters | Farm Structure |
| super clusters | Ecosystem Group |
| second hop | Funding Lineage |
| creator self funding | Self-Funding |
| network risk | Ecosystem Risk |
| historical performance | Historical Outcomes |
| profitability | Simulation PnL |

## Terminology rule
If the name answers “how was it computed?” rather than “what does it mean?”, it is probably wrong for the UI.

---

# G. Reusable intelligence component system

## 1. Ecosystem Quality Card
- quality score
- risk score
- confidence
- historical / simulation split

## 2. Creator Expectancy Card
- migration
- rug
- runner production
- simulation ROI

## 3. Coordinator Influence Card
- creator reach
- overlap density
- operator concentration

## 4. Historical Outcome Strip
- total tokens
- median peak multiple
- 2x / 5x / 10x runners
- survival
- coverage

## 5. Simulation vs Historical Coverage Card
- historical tokens
- simulated tokens
- sample coverage
- “strategy has only observed X% of this ecosystem”

## 6. Funding Lineage Card
- direct funder
- upstream hub
- CEX / infra / AML labels

## 7. Liquidity Survival Card
- current liquidity
- migration survival
- liquidation behavior

## 8. Risk Composition Card
- creator risk mix
- token prediction mix
- liquidation operators

## 9. Related Entities Rail
- creator
- ecosystem
- coordinator
- similar / linked entities

---

# H. Intelligence synthesis summaries

Every entity page starts with:

```text
Why should I care?
```

## Summary framework

### Token
- who launched it
- whether that creator / ecosystem has expectancy
- whether liquidity / risk makes it actionable

### Creator
- historical quality
- operator pattern
- ecosystem context

### Ecosystem
- outcome quality
- coordination level
- confidence / coverage

### Funder
- funded creator quality
- coordinator influence
- structural risk

## Summary examples

- “This ecosystem historically produces strong runners with low rug rates and moderate coordinator concentration.”
- “This creator self-funds, belongs to a weak ecosystem, and has poor liquidity survival.”
- “This funder reaches many creators, but most belong to high-risk rotation operators.”
- “This token is low-risk individually, but comes from an ecosystem with thin historical evidence.”

## Presentation rule
Summary first. Tables second. Evidence third.

---

# I. Single-pane-of-glass workflows

## New token launch
1. User lands on **Live Market**.
2. Opens token.
3. Token page immediately shows:
   - prediction
   - creator expectancy
   - ecosystem quality
   - funding lineage
   - liquidity survival
4. One click to creator or ecosystem.
5. User compares:
   - Historical Outcomes
   - Simulation PnL
6. User decides whether capital allocation is justified.

## Creator review
1. User opens Creator page.
2. Sees launch history + synthesized summary.
3. Opens Funding lens and Graph lens on same page.
4. Drills into ecosystem or coordinator only if needed.

## Ecosystem review
1. User opens Ecosystem page.
2. Overview says whether quality is real, concentrated, or thinly evidenced.
3. Tabs expose structure, history, simulation PnL, liquidity, risk.
4. User can distinguish:
   - organic quality
   - coordinator-driven quality
   - toxic rotation networks

---

# J. Migration / refactor plan

## Phase 1 — Label and navigation cleanup
- rename pages conceptually
- remove redundant top-level links
- group diagnostics

## Phase 2 — Build canonical entity pages
- Token
- Creator
- Ecosystem
- Funder / Coordinator
- Portfolio Position

## Phase 3 — Embed lenses
- graph
- shared funding
- farm structure
- risk
- historical outcomes
- simulation PnL

## Phase 4 — Redirect legacy pages
- keep old URLs working
- route them to the right canonical entity + selected tab

## Phase 5 — Retire table dumps from primary UX
- preserve as admin / export views
- stop making users navigate by subsystem

---

# K. Admin / diagnostic segregation

## Move under System / Diagnostics
- Webhook Monitor
- Funding Queue
- Snapshots
- Vaults
- Usage
- System Health
- Raw Graph Diagnostics
- Analyzer Freshness

## Keep accessible but not primary
- Approval Queue
- Network Approval
- Spike Analysis
- Pump Bots
- Investigations

---

# L. Highest-leverage simplifications

1. Collapse many graph pages into one Ecosystem Intelligence area.
2. Make Creator / Ecosystem / Funder pages canonical dossiers.
3. Remove analyzer names from navigation.
4. Keep Historical Performance and Simulation PnL adjacent but separate.
5. Replace raw table-first layouts with synthesis-first layouts.
6. Demote diagnostics out of the action path.
7. Reuse the same cards everywhere so users learn one intelligence grammar.
8. Surface confidence / coverage beside every score.

---

# M. Final product shape

FLEX becomes:

```text
A cockpit for understanding ecosystems,
not a filing cabinet of analyzers.
```

The user should be able to answer, from one connected flow:

```text
What launched?
Who is behind it?
What system are they part of?
Is that system good?
How coordinated is it?
What has it produced before?
What have we actually earned there?
How confident are we?
Should we allocate capital?
```
