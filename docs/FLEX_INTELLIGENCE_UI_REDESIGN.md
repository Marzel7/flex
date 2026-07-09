# FLEX Intelligence UI Redesign

_Last updated: 2026-05-15_

## Executive summary

FLEX has become an ecosystem-intelligence platform, but the UI still reflects the order in which subsystems were built. The product should no longer be organized around implementation artifacts such as `wallet_clusters`, `funder_overlap`, and separate table pages. It should be organized around the entities users reason about:

```text
Token → Creator → Ecosystem → Funding Structure → Prediction → Performance
```

The redesign should:
- keep every analyzer and table intact
- reduce top-level page count
- promote decision signals over raw internal mechanics
- make each canonical entity page a complete dossier
- keep diagnostics available, but subordinate
- preserve the hard boundary between **Historical Ecosystem Performance** and **Simulation PnL**

The central product move is this:

```text
from: many pages that each reveal one subsystem
  to: a few entity pages that compose all relevant subsystems
```

---

# A. Current-page audit

## Legend
- **Primary** — user-facing destination that should survive as a major concept
- **Supporting** — useful lens or drilldown, but not top-level
- **Legacy** — overlaps with a stronger destination and should be folded away
- **Redundant** — largely duplicative after consolidation
- **Diagnostic** — admin / implementation / ops surface

| Current page | Concept | Main data / signals | Overlap | Status |
|---|---|---|---|---|
| `/` | live market | live tokens, MC, price, migrations | pumpfun, token intelligence | Primary |
| `/pumpfun` | source-specific live tokens | PumpFun feed | live market | Supporting |
| `/token-intelligence` | token dossier / token signals | lifecycle, behavior, token analysis | predictions, risk scoring | Primary but should become canonical token view |
| `/predictions` | actionable prediction queue | token prediction scores/events | risk scoring, approval queue | Primary |
| `/approval-queue` | token review workflow | prediction review | predictions | Supporting / workflow |
| `/network-approval` | network review workflow | network review status | ecosystems / risk | Supporting / workflow |
| `/risk-scoring` | risk model output | creator/network/token risk scores | token intelligence, creator analysis, ecosystems | Supporting; not primary |
| `/trading-sim` | simulation portfolio | strategy performance, positions, portfolio equity | profitable-* lenses | Primary |
| `/networks` | released networks | network_membership, networks_release | network-intelligence, ecosystem-networks | Legacy as standalone |
| `/network-intelligence` | joined ecosystem context | networks + farm + coordinator context | networks, clusters, coordinators | Primary concept but should merge into Ecosystems |
| `/clusters` | farm clusters | farm_clusters, members, risk | ecosystems, network intelligence | Supporting lens |
| `/coordinators` | coordinator wallets | wallet_clusters | funders, coordinated-funders, ecosystem-funders | Supporting lens |
| `/coordinated-funders` | overlap / coordination | network_coordinators, coordinated funders | coordinators, transfer graph, ecosystem-funders | Supporting lens / likely fold-in |
| `/transfer-graph` | raw graph operations | transfer_index, graph analyzer freshness | creator/funder/network graph context | Supporting / diagnostic hybrid |
| `/creator-analysis` | creator dossier | creator scans, outgoing transfers, funding, network context | ecosystem-creators, risk scoring | Primary but should become canonical creator view |
| `/funder-intelligence` | funder dossier | funder activity / chains | coordinators, coordinated-funders, ecosystem-funders | Primary but should become canonical funder/coordinator view |
| `/top-funding-hubs` | upstream hubs | upstream hubs, reach | funder intelligence, transfer graph | Supporting |
| `/ecosystem` | ecosystem landing | historical + sim lenses | networks / clusters / coordinators | Primary |
| `/ecosystem-creators` | creator historical lens | creator historical performance | creator-analysis | Primary lens within Creators |
| `/ecosystem-networks` | ecosystem historical lens | network historical performance | networks, network-intelligence | Primary lens within Ecosystems |
| `/ecosystem-funders` | funder historical lens | funder historical performance | funder-intelligence, coordinators | Primary lens within Funders |
| `/ecosystem-clusters` | cluster historical lens | cluster historical performance | clusters | Supporting lens |
| `/profitable-creators` | creator sim lens | creator_profitability | ecosystem-creators | Should not remain top-level; keep as Simulation PnL lens |
| `/profitable-networks` | network sim lens | network_profitability | ecosystem-networks | Same |
| `/profitable-funders` | funder sim lens | funder_profitability | ecosystem-funders | Same |
| `/network-diagram` | investigations | curated case studies | network intelligence | Supporting |
| `/spike-analysis` | flash spikes | spike signals | token intelligence | Supporting / specialist |
| `/pump-bots` | pump bot intelligence | bot signals | token intelligence / ecosystems | Supporting |
| `/webhook-monitor` | ingestion ops | webhook queue / metrics | transfers | Diagnostic |
| `/funding-queue` | extraction ops | queue performance | funding | Diagnostic |
| `/snapshots` | snapshot ops | snapshot history / counts | token intelligence | Diagnostic |
| `/vaults` | pool/vault ops | vault discovery | token infra | Diagnostic |
| `/system-health` | service health | workers, first snapshot health, DB | all systems | Diagnostic |
| `/usage` | usage metrics | RPC/API usage | system | Diagnostic |
| `/settings` | configuration | settings | system | Diagnostic |

---

# B. Overlap map

## 1. Networks / clusters / graph

```text
/networks
/network-intelligence
/clusters
/coordinators
/coordinated-funders
/transfer-graph
```

All describe facets of one broader idea: **ecosystem structure**.

Recommended interpretation:
- **Ecosystem** = the user-facing object
- **Farm Structure** = one structural lens
- **Coordinator Structure** = one structural lens
- **Shared Funding** = one structural lens
- **Transfer Graph** = low-level evidence / drilldown

## 2. Creator views

```text
/creator-analysis
/ecosystem-creators
/risk-scoring
```

All speak to creator quality. They should collapse into one canonical creator dossier with tabs/cards for:
- overview
- funding
- graph
- risk
- historical outcomes
- simulation PnL

## 3. Funder views

```text
/funder-intelligence
/coordinators
/coordinated-funders
/ecosystem-funders
/top-funding-hubs
```

All describe capital origin and organization. They should become one canonical **Funder / Coordinator** entity model.

## 4. Token intelligence

```text
/token-intelligence
/predictions
/risk-scoring
/spike-analysis
/pump-bots
```

All describe token quality and actionability. `Predictions` remains a queue; the token page becomes the unified dossier.

## 5. Performance

```text
/ecosystem-*
/profitable-*
/trading-sim
```

These are not duplicates, but two lenses over different truths:
- Historical Performance
- Simulation PnL

They belong together visually, never mathematically merged.

---

# C. Proposed navigation tree

## Recommended top-level navigation

```text
1. Live Market
   - Live Feed
   - PumpFun Feed

2. Tokens
   - Token Explorer
   - Predictions
   - Approval Queue

3. Creators
   - Creator Explorer
   - Creator Watch / Review

4. Ecosystems
   - Overview
   - Ecosystem Explorer
   - Farm Structure lens
   - Investigations

5. Funding Graph
   - Funder Explorer
   - Funding Lineage
   - Coordinator / Shared Funding lens
   - Upstream Hubs

6. Portfolio
   - Simulation Portfolio
   - Strategy Performance

7. Diagnostics
   - System Health
   - Webhook Monitor
   - Funding Queue
   - Snapshots
   - Vaults
   - Usage
   - Settings
```

## What disappears from top-level nav
- standalone `Profitable Creators / Networks / Funders`
- standalone `Coordinators`
- standalone `Coordinated Funders`
- standalone `Clusters`
- standalone `Transfer Graph`

They remain accessible as lenses, tabs, or drilldowns.

---

# D. Canonical entity designs

## 1. Token page

### Header
- symbol / mint
- live MC / price
- prediction label
- lifecycle stage
- creator
- ecosystem membership

### Sections
1. **Overview**
   - prediction status
   - lifecycle stage
   - migration state
   - first observed MC / current MC / peak MC
2. **Risk**
   - token risk label
   - behavior label
   - liquidity risk
   - creator risk
3. **Funding Context**
   - creator funders
   - self-funding
   - upstream lineage
4. **Graph Context**
   - ecosystem
   - coordinator exposure
   - farm / cluster membership
5. **Performance**
   - Historical: peak multiple, current multiple, survival
   - Simulation: actual position if traded, strategy outcomes
6. **Related entities**
   - creator
   - funder/coordinator
   - ecosystem

## 2. Creator page

### Header
- wallet
- creator quality score
- creator risk
- ecosystem memberships
- token count

### Sections
1. **Overview**
   - total launches
   - latest launches
   - migration / rug / survival mix
2. **Historical Outcomes**
   - token count
   - runner counts
   - median peak multiple
   - first-MC coverage
3. **Simulation PnL**
   - deployed / realised / unrealised / equity / ROI
   - strategy breakdown
4. **Funding**
   - funders
   - self-funding
   - upstream lineage
5. **Graph Structure**
   - coordinator exposure
   - creator-to-creator transfers
   - farm membership
6. **Risk**
   - creator risk score
   - risk reasons
   - behavior history
7. **Related**
   - networks
   - funders
   - similar creators

## 3. Ecosystem / network page

### Header
- display name
- size
- risk level
- historical quality score
- simulation quality score

### Sections
1. **Overview**
   - creators
   - tokens
   - dominant funders
   - coordinator count
2. **Historical Performance**
   - migration
   - survival
   - behavior mix
   - multiples / coverage
3. **Simulation PnL**
   - deployed / equity / PnL / ROI
4. **Structure**
   - shared funding
   - overlap density
   - coordinator concentration
   - farm concentration
5. **Risk Composition**
   - high/medium/low creator mix
   - serial ruggers
   - liquidation operators
6. **Membership**
   - creators
   - funders
   - farm substructures
7. **Related ecosystems**
   - bridge links
   - second-hop links

## 4. Funder / coordinator page

### Header
- wallet
- coordinator status
- creator reach
- quality / risk labels

### Sections
1. **Overview**
   - creators funded
   - total funded SOL
   - networks touched
2. **Historical Expectancy**
   - outcomes of funded creators
   - migration / runner / rug composition
3. **Simulation Attribution**
   - ROI of creators actually traded by the strategy
4. **Coordination**
   - overlap peers
   - wallet cluster
   - shared-funder density
5. **Lineage**
   - upstream sources
   - second-hop bridges
6. **Risk**
   - CEX / infra / AML labels
   - operator pattern
7. **Related**
   - creators
   - ecosystems
   - overlap groups

## 5. Cluster / farm page

### Header
- cluster id
- size
- risk level
- farm quality

### Sections
- token output
- historical performance
- simulation coverage
- member roles
- funder concentration
- ecosystem concentration
- coordinator overlap
- liquidity / liquidation pattern

## 6. Prediction page

### Canonical role
A queue, not a dossier.

### Should show
- actionable tokens
- confidence
- reason summary
- direct drill-through to token / creator / ecosystem pages

## 7. Simulation position page

### Sections
- entry thesis
- strategy state
- realised vs unrealised
- cascade/target/watch exits
- linked creator / ecosystem historical context

---

# E. Consolidation recommendations

## Merge / fold

### 1. `/networks` + `/network-intelligence`
Create one **Ecosystem Explorer**.
- table mode = current `/networks`
- dossier / enriched side panel = current `/network-intelligence`

### 2. `/clusters`
Fold into Ecosystem as:
- `Farm Structure` lens
- dedicated drilldown page only when user opens a cluster

### 3. `/coordinators` + `/coordinated-funders`
Fold into **Funding Graph** and Funder dossiers.
- coordinator wallet = entity type / badge
- coordinated funder pair = evidence, not a standalone kingdom

### 4. `/transfer-graph`
Keep, but demote from primary nav.
- use as **Graph Evidence** drilldown
- embed graph snippets inside creator / ecosystem / funder pages

### 5. `/creator-analysis` + `/ecosystem-creators`
Become one **Creator Explorer** with two lenses:
- Historical Performance
- Simulation PnL

### 6. `/risk-scoring`
Demote to supporting / model-explanation page.
- risk should appear directly in token / creator / ecosystem dossiers

### 7. `/profitable-*`
Keep URLs for now, but remove from nav.
- they become the **Simulation PnL** lens inside Creator / Ecosystem / Funder pages

---

# F. Unified terminology proposal

| Current technical term | User-facing term |
|---|---|
| `network_membership`, `networks_release` | Ecosystem |
| `wallet_clusters` | Coordinator Structure |
| `network_coordinators` | Coordinator |
| `funder_overlap` | Shared Funding |
| `coordinated_creator_edges` | Coordinated Creators |
| `creator_c2c_edges` | Creator Transfers |
| `farm_clusters` | Farm Structure |
| `second hop`, `upstream bridge` | Funding Lineage |
| `creator_self_funding` | Self-Funding |
| `network risk score` | Ecosystem Risk |
| `creator historical performance` | Historical Outcomes |
| `creator profitability` | Simulation PnL |

## Vocabulary principle
Expose what the user needs to understand, not the table that happened to compute it.

---

# G. Reusable UI component plan

## 1. Creator Expectancy Card
Use on:
- token page
- creator page
- ecosystem membership tables

Shows:
- historical score
- simulation ROI
- migration rate
- rug rate
- first-MC coverage

## 2. Ecosystem Quality Card
Shows:
- historical quality
- simulation quality
- coordinator concentration
- farm concentration
- high-risk creator mix
- confidence / coverage

## 3. Coordinator Risk Card
Shows:
- coordinator flag
- creator reach
- overlap density
- repeat rug / liquidation association

## 4. Historical Outcome Strip
Shows:
- total tokens
- 2x / 5x / 10x counts
- median peak x
- survival
- coverage

## 5. Funding Lineage Card
Shows:
- direct funder
- upstream hub
- CEX / infra labels
- second-hop bridges

## 6. Simulation vs Historical Coverage Card
Shows side by side:
- historical tokens
- simulated tokens
- coverage %
- “strategy traded only X% of this ecosystem”

## 7. Liquidity Survival Card
Shows:
- migration
- live liquidity
- liquidation behavior
- post-migration decay

## 8. Ecosystem Composition Card
Shows:
- creator count
- risk distribution
- coordinator count
- farm substructures
- dominant funders

---

# H. Single-pane-of-glass workflow examples

## Workflow 1: new token launch

1. User lands on **Live Market**.
2. Opens a token row.
3. Token dossier shows:
   - prediction
   - liquidity
   - creator expectancy
   - ecosystem membership
   - funding lineage
4. User clicks creator.
5. Creator dossier shows:
   - historical outcomes
   - simulation PnL
   - funding / coordinator context
6. User clicks ecosystem.
7. Ecosystem dossier answers:
   - Is this ecosystem historically good?
   - Is it coordinator-driven?
   - Has our strategy traded it profitably before?

## Workflow 2: review a suspicious creator

1. User enters via **Creators** or token drilldown.
2. Sees creator risk and historical outcomes together.
3. Opens Graph Context:
   - coordinator exposure
   - creator transfers
   - farm structure
4. Opens related ecosystem / funder from same page.

## Workflow 3: compare strategy vs ecosystem truth

1. User opens an ecosystem.
2. Historical tab shows broad outcomes.
3. Simulation tab shows actual policy performance.
4. Coverage card explains whether the strategy sampled the ecosystem deeply or barely touched it.

---

# I. Migration / refactor plan

## Phase 1 — Navigation cleanup
- remove `profitable-*` from sidebar
- add grouped `Ecosystem`
- move diagnostic pages under `System / Diagnostics`
- rename labels without changing routes yet

## Phase 2 — Canonical entity dossiers
- token dossier
- creator dossier
- ecosystem dossier
- funder/coordinator dossier
- cluster dossier

## Phase 3 — Fold legacy pages into lenses
- networks + network-intelligence
- clusters into ecosystem lens
- coordinators + coordinated-funders into funder/coordinator lens
- transfer graph as evidence panel

## Phase 4 — Reusable cards
- build common components once
- use across token / creator / ecosystem / funder pages

## Phase 5 — Redirect / deprecate
- preserve old routes
- redirect legacy destinations to canonical entities with relevant tab selected

---

# J. Pages that should become internal / admin only

## Strong candidates
- `/webhook-monitor`
- `/funding-queue`
- `/snapshots`
- `/vaults`
- `/usage`
- `/system-health`
- raw `/transfer-graph` if not in investigation mode

## Keep accessible, but not in the primary decision workflow
- `/approval-queue`
- `/network-approval`
- `/spike-analysis`
- `/pump-bots`
- `/network-diagram`

---

# K. Primary vs secondary intelligence

## Primary intelligence
These are signals users should act on directly:
- prediction label / confidence
- creator quality
- ecosystem quality
- funding lineage risk
- coordinator concentration
- historical outcomes
- simulation PnL
- liquidity survival
- coverage confidence

## Secondary intelligence
These support explanation, debugging, and investigation:
- raw overlap pairs
- raw edge counts
- queue states
- analyzer freshness
- vault discovery details
- raw snapshot rows
- low-level cluster internals

The UI should surface primary signals first and let users descend into secondary evidence deliberately.

---

# L. Ecosystem-quality score framework

## Score families

### Outcome quality
- migration success
- survival
- behavior composition
- runner density
- first-MC multiples

### Structural quality
- coordinator concentration
- farm concentration
- self-funding dominance
- overlap density
- creator diversity

### Strategy quality
- simulation ROI
- equity retention
- sampled coverage

### Confidence / coverage
- first-MC coverage
- market-data coverage
- simulation coverage
- number of launches

## Presentation rule
Never present one naked score. Present:

```text
Quality 78
Confidence Medium
Why: strong migration + survival, but only 22% first-MC coverage
```

That keeps the interface intelligent rather than falsely certain.

---

# M. Highest-value simplifications

1. Replace implementation nouns with entity nouns.
2. Collapse `Networks / Network Intelligence / Clusters / Coordinators` into **Ecosystems** + lenses.
3. Collapse `Creator Analysis / Ecosystem Creators / Risk Scoring` into **Creator** dossiers.
4. Collapse `Funder Intelligence / Coordinators / Coordinated Funders` into **Funder / Coordinator** dossiers.
5. Keep **Historical Performance** and **Simulation PnL** adjacent but separate everywhere.
6. Move operational pages out of the main decision path.
7. Reuse the same intelligence cards across pages so the product teaches one mental model repeatedly.

---

# N. Bottom line

FLEX should feel less like a collection of successful subsystems and more like a cockpit:

```text
What just launched?
Who is behind it?
What ecosystem does it belong to?
How is that ecosystem organized?
What has it historically produced?
What has our strategy actually earned there?
How confident should I be?
```

If every major page helps answer one of those questions, the platform becomes coherent without sacrificing any of its depth.
