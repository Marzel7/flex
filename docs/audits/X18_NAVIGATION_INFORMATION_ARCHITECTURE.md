# Sprint X18 — Navigation & Information Architecture

Status: implemented navigation shell and design specification, 2026-07-13.

## 1. Decision

The UI should be organised around an investigation, not around the subsystem that generated the data.

The canonical analyst path is:

```text
Mission Control
  → Discovery (how was this found?)
  → Entity (what is it?)
  → Operator (who controls it?)
  → Assessment (what do we conclude?)
  → Forecast (what may happen next?)
```

Operations, Review, and Analysis are supporting workspaces around that path. Feature-era pages remain reachable, but are no longer presented as peers of canonical intelligence.

## 2. New navigation hierarchy

```text
Mission Control                         /ops-os

Investigate
  Discovery                             /discovery
  Entity Intelligence                   /intelligence/entity/<id>
  Operators                             /intelligence/operators

Operations
  Live Operations                       /ops
  WATCHTOWER                            /watchtower/intelligence
  Launcher Observatory                  /ops-os/launcher-observatory
  Buy Swarm Observatory                 /ops-os/buy-swarm-observatory

Review
  Analyst Inbox                         /intelligence/inbox
  Promotion Review                      /intelligence/operator-promotions

Analysis
  Cross-Operation                       /intelligence/cross-operation
  Knowledge                             /intelligence/knowledge

Legacy Features                        collapsed, explicitly labelled
Legacy WATCHTOWER                       collapsed, explicitly labelled
System                                  collapsed
```

Entity Intelligence is contextual: analysts normally arrive with an entity from Discovery, Inbox, an operation, or another intelligence page. Direct navigation to `/intelligence/entity/` now returns the analyst to Discovery instead of a broken page.

## 3. Analyst journeys

### Known operator

```text
Mission Control → Operators → Operator
                              ├─ current answer
                              ├─ supporting behaviour/change
                              └─ raw observations/evidence
```

- Mission Control identifies attention or provides operator search.
- Operators locates the canonical actor.
- Operator owns the actor narrative, assessment, and forecast.
- Discovery is a backwards link when the analyst asks how attribution was established.

### Unknown entity

```text
Mission Control → Discovery → Entity → linked Operator → Assessment
```

- Discovery establishes provenance.
- Entity establishes role and appearances.
- The linked Operator provides actor-level interpretation.
- If no operator exists, Entity ends with a promotion/review state rather than a dead end.

### Promotion review

```text
Mission Control → Analyst Inbox → Promotion Review
                                  → Operator created
                                  → observations materialised
                                  → Operator Intelligence
```

- Inbox owns attention and prioritisation.
- Promotion Review owns the governance decision.
- Operator owns all post-approval intelligence.

### Current campaign

```text
Mission Control → Live Operations → Operation → Entity or Operator
```

- Live Operations answers what is active.
- Operation provides campaign context.
- Entity and Operator continue the attribution investigation.

### Historical investigation

```text
Discovery search → Discovery provenance → Entity timeline
                                       → Operator history
                                       → Cross-Operation overlap
```

### Behaviour review

```text
Operator → Behaviour answer → Behaviour Change → supporting observations
```

Behaviour and Behaviour Change are sections of Operator, not separate destinations. Raw observations are Level 3 evidence.

### Assessment and forecast

```text
Operator → Assessment → Forecast → evidence/observation drilldown
```

Assessment answers what the platform concludes. Forecast answers what may happen next. Neither should force the analyst to navigate to a feature dashboard.

## 4. Page ownership matrix

| Canonical page | One-sentence ownership | Inputs | Output | Natural next step |
|---|---|---|---|---|
| Mission Control | “What requires analyst attention?” | lifecycle, inbox, active operations, promotions, discoveries | ranked situation and next actions | relevant investigation/review page |
| Discovery | “How did we establish this?” | provenance records and discovery sources | ordered attribution history | Entity |
| Entity | “What do we know about this entity?” | identity facts, appearances, roles, relationships | entity identity and linked operator | Operator or Discovery |
| Operators | “Which canonical actors are known?” | canonical operator registry | searchable actor list | Operator |
| Operator | “What do we know about this actor?” | identity, observations, behaviour, change, assessment, forecast | coherent actor dossier | evidence, related entity, review |
| Live Operations | “What is active now?” | operation lifecycle and activity | active campaign situation | Operation |
| Operation | “What is happening in this campaign?” | operation-scoped capabilities | campaign state and participants | Entity or Operator |
| Analyst Inbox | “What requires my decision or acknowledgement?” | lifecycle and governance attention items | prioritised work queue | Review or investigation |
| Promotion Review | “Should this proposed identity become canonical?” | proposal, evidence snapshot, prior reviews | governed decision | Operator |
| Cross-Operation | “Where does intelligence overlap?” | entity/operator relationships | overlaps and shared infrastructure | Entity or Operator |
| Knowledge | “Why did the platform infer this?” | rules, claims, provenance | explanation of inference | originating Entity/Operator |
| System Health | “Can the analyst trust that the platform is operating?” | service and data freshness telemetry | health state | specific diagnostic |

Ownership boundaries:

- Discovery owns provenance, not identity summaries.
- Entity owns entity role and appearances, not operator behaviour.
- Operator owns actor interpretation, assessment, and forecast.
- Inbox owns attention, not evidence presentation.
- Promotion Review owns the decision, not the resulting actor dossier.
- Knowledge owns inference explanation, not a second evidence timeline.

## 5. Three-level presentation rule

Every canonical page uses the same information depth:

| Level | Purpose | Default state | Examples |
|---|---|---|---|
| 1 — Answer | The conclusion needed to orient the analyst | visible in first viewport | current state, identity, assessment, next action |
| 2 — Supporting intelligence | Why the answer is credible | visible summary or expandable | behaviour dimensions, relationships, confidence contributors |
| 3 — Raw evidence | Engineering and forensic detail | collapsed | source rows, raw JSON, full observation timeline, telemetry |

Rules:

1. No raw table appears before the Level 1 answer.
2. Confidence appears once beside the claim it qualifies.
3. A timeline has one owner; other pages link to or filter it.
4. “No data” must describe lifecycle state and the next expected event.
5. A page should expose at most one primary action in Level 1.

## 6. Current page audit and disposition

### Canonical surfaces

| Page / route | Purpose and primary question | Inputs → outputs | Next | Disposition |
|---|---|---|---|---|
| `/ops-os` | Mission Control: what needs attention now? | registry, lifecycle, inbox → situation | investigate/review/operation | keep; canonical home |
| `/discovery` | How was an entity/operator established? | provenance → discovery history | Entity | keep; provenance only |
| `/intelligence/entity/<id>` | What is this entity and where has it appeared? | entity facts/relationships → identity | Operator | keep; simplify to entity scope |
| `/intelligence/operators` | Which actors are known? | canonical registry → lookup | Operator | keep as registry |
| `/intelligence/operator/<id>` | What do we know about this actor? | full reasoning pipeline → dossier | evidence/entity/review | keep; canonical conclusion page |
| `/intelligence/inbox` | What needs analyst attention? | attention items → prioritised actions | appropriate owner page | keep |
| `/intelligence/operator-promotions` | Should this attribution become canonical? | proposals/evidence → decision | Operator | keep |
| `/intelligence/cross-operation` | Where does intelligence overlap? | relationships → overlaps | Entity/Operator | keep |
| `/intelligence/knowledge` | Why was this inferred? | rules/provenance → explanation | source dossier | keep |
| `/ops` and `/ops/live` | What operations are active? | live lifecycle → active operations | operation detail | keep as Operations landing |
| `/ops/operation/<uuid>` | What is happening in this operation? | scoped operation data → campaign dossier | Entity/Operator | keep |
| `/ops-os/<operation_id>` | What capabilities and posture does this operation expose? | registry/provider contracts → operation summary | live operation/detail | contextual operation profile |

### Operation-specific surfaces

| Page / route | Primary question | Next | Disposition |
|---|---|---|---|
| `/watchtower/intelligence` | What is WATCHTOWER doing? | operation/entity/operator | keep under Operations |
| `/ops-os/launcher-observatory` | What is the launcher operation doing? | entity/operator | keep under Operations |
| `/ops-os/buy-swarm-observatory` | What is the buy-swarm operation doing? | entity/operator | keep under Operations |
| `/watchtower/operations` | Which WATCHTOWER campaigns exist? | operation detail | merge/contextual under WATCHTOWER |
| `/watchtower/operator/<address>` | What did the legacy WATCHTOWER model know about this wallet? | canonical Entity/Operator | legacy contextual |
| `/watchtower/candidate/<mint>` | What evidence exists for this candidate? | Entity/Promotion | legacy contextual |
| `/watchtower/dashboard`, `/command-center`, `/watchtower` | What is WATCHTOWER’s overall status? | WATCHTOWER operation page | merge into WATCHTOWER/Mission Control; routes retained |

### Legacy feature and review surfaces

| Page / route | Primary question | Canonical destination | Disposition |
|---|---|---|---|
| `/live-launches`, `/pumpfun` | Which tokens launched / are approaching launch? | Live Operations | legacy drilldown |
| `/token-intelligence`, `/token-behaviour` | What happened to this token? | Entity | merge into Entity evidence |
| `/creator-analysis`, `/creators` | What do we know about this creator? | Entity | merge into Entity |
| `/funder-intelligence`, `/funding`, `/top-funding-hubs`, `/funding-hub/<id>` | What funding infrastructure exists? | Entity/Cross-Operation | contextual drilldown |
| `/coordinated-funders`, `/coordinated-funder-analysis/<id>` | Which funders coordinate? | Cross-Operation | contextual drilldown |
| `/clusters`, `/coordinators` | Which entities cluster together? | Cross-Operation | contextual drilldown |
| `/network-intelligence`, `/networks`, `/network-diagram` | What networks/graphs exist? | Cross-Operation | consolidate; legacy routes retained |
| `/network-diagram/htx`, `/network-diagram/okx`, `/network-diagram/watchtower`, `/network-diagram/coinbase-cluster` | Show a predefined graph | Cross-Operation filtered view | legacy contextual |
| `/transfer-graph` | What raw transfers connect these nodes? | Entity/Cross-Operation evidence | Level 3 diagnostic |
| `/spike-analysis` | Which transient price spikes occurred? | Entity evidence | legacy contextual |
| `/risk-scoring` | Which entities rank as risky? | Assessment | merge into assessment presentation |
| `/predictions` | Which legacy token predictions exist? | Operator Forecast | legacy review/reference |
| `/trading-sim`, `/portfolio` | What would the trading simulation do? | none in canonical investigation | separate legacy tool |
| `/approval-queue` | Which tokens await review? | Inbox | legacy queue surfaced through Inbox |
| `/funding-queue` | Which funding records await review? | Inbox | legacy queue surfaced through Inbox |
| `/network-approval` | Which ecosystem identities await review? | Inbox/Promotion Review | legacy governance queue |
| `/ecosystem`, `/ecosystems`, `/ecosystem-creators`, `/ecosystem-networks`, `/ecosystem-funders`, `/ecosystem-clusters` | Browse historical ecosystem datasets | Entity/Cross-Operation | legacy reference family |
| `/profitable-creators`, `/profitable-networks`, `/profitable-funders` | Rank historical profitability | Entity/Analysis | legacy reference |
| `/snapshots`, `/vaults` | Inspect historical model artefacts | Entity/Knowledge Level 3 | legacy reference |
| `/launch-radar`, `/launch-waves`, `/dev-clusters`, `/org-explorer`, `/signal-explorer`, `/early-signals`, `/organization/<id>`, `/fingerprint/<id>`, `/wallet/<id>` | Explore pre-canonical FLEX feature views | Discovery/Entity/Cross-Operation | legacy feature family |

### Legacy WATCHTOWER operational tools

| Page / route | Primary question | Canonical destination | Disposition |
|---|---|---|---|
| `/watchtower/operators` | Which legacy WATCHTOWER identities exist? | Operators | legacy registry |
| `/watchtower/interceptor` | Is the interceptor ready? | WATCHTOWER operation | contextual tool |
| `/ops/tokens` | How did WATCHTOWER tokens perform? | Entity/Operator outcome | legacy drilldown |
| `/ops/cards`, `/ops/operations` | Alternative live-operation presentations | Live Operations | merge presentation |
| `/ops/detection-health` | Is detection coverage healthy? | System/operation diagnostics | contextual diagnostic |
| `/ops/discovery-assurance` | Are discovery paths complete? | Discovery Level 3/System | contextual diagnostic |
| `/ops/webhook-coverage` | Are WATCHTOWER webhooks complete? | System Health | contextual diagnostic |
| `/ops/dust-observatory` | What dust-signalling activity exists? | Entity evidence | contextual diagnostic |

### System and engineering surfaces

| Page / route | Primary question | Next | Disposition |
|---|---|---|---|
| `/system-health` | Is the platform healthy? | targeted diagnostic | keep under System |
| `/webhook-monitor`, `/webhook-metrics` | Are webhook feeds healthy? | System Health | contextual diagnostic |
| `/network-monitoring` | Are monitored networks healthy? | System Health | contextual diagnostic |
| `/usage`, `/rpc-savings-dashboard` | What resources are being consumed? | System Health | contextual diagnostic |
| `/db-serializer` | How is the managed write lane behaving? | System Health | engineering Level 3 |
| `/settings` | How is the platform configured? | return to prior page | keep under System |
| `/test-prices`, `/ws/tokens` | Developer/test surfaces | none | hidden engineering route |

No route is deleted in X18.

## 7. Duplication rationalisation

| Repeated concept | Current duplication | Canonical owner | Reuse rule |
|---|---|---|---|
| Confidence | Entity, Operator, Discovery, cards, tables | claim component on owning page | one confidence badge + contributor disclosure |
| Evidence | Discovery, Entity, Operator, promotion, legacy detail | Discovery for provenance; owning dossier for filtered support | shared evidence row component |
| Timeline | Entity, Operator, operations, behaviour | owning dossier | shared timeline component with filters, never copied |
| Lifecycle | Mission Control, Operation, Operator, Inbox | lifecycle status component | same vocabulary and colour mapping |
| Relationships | Entity, Cross-Operation, network pages | Cross-Operation graph; Entity summary | shared relationship edge/card |
| Review history | promotion and operator | Promotion owns decision log; Operator links | shared review event component |
| Search | many page-local boxes | Mission Control global entry + contextual filters | one global search behavior |
| Metrics/cards | mission, operations, observatories | page-specific Level 1 answer | shared metric primitive, no repeated metric sets |
| Inbox/queues | Inbox plus three legacy queues | Inbox | legacy queues become filtered sources |
| Assessment/forecast | operator sections and legacy prediction/risk pages | Operator | no parallel conclusion pages |

## 8. Component reuse plan

Canonical UI primitives:

1. `AnswerHeader` — question, current answer, confidence, freshness, one primary action.
2. `LifecycleBadge` — shared lifecycle vocabulary and colours.
3. `ConfidenceBadge` — level plus expandable contributing facts.
4. `EvidenceRow` — source, timestamp, claim, provenance link.
5. `InvestigationTimeline` — shared chronology with type/source filters.
6. `EntityLink` and `OperatorLink` — consistent labels and forward navigation.
7. `RelationshipCard` — source entity, relation, target, confidence.
8. `ReviewEvent` — decision, reviewer, timestamp, immutable evidence reference.
9. `EmptyLifecycleState` — what has happened, what is waiting, what comes next.
10. `RawEvidenceDisclosure` — collapsed Level 3 container.

Implementation order should begin with components that currently have the most inconsistent semantics: Lifecycle, Confidence, Evidence, and Timeline.

## 9. Legacy migration plan

### Stage 1 — navigation (implemented in X18)

- Canonical hierarchy becomes primary.
- Legacy route families are collapsed and labelled.
- `/` points to Mission Control.
- Existing routes and bookmarks continue working.

### Stage 2 — contextual entry

- Add canonical “continue investigation” links to legacy detail pages.
- Add Legacy banners explaining the canonical destination.
- Mission Control and Inbox deep-link directly to the owning page.

### Stage 3 — component convergence

- Replace duplicated confidence, lifecycle, timeline, and evidence presentations with shared primitives.
- Move raw tables into Level 3 disclosures.
- Preserve APIs and routes.

### Stage 4 — content absorption

- Absorb unique useful content from legacy pages into Entity, Operator, Operation, or Cross-Operation.
- Convert old pages to filtered/contextual views where needed.
- Measure route use before considering redirects.

### Stage 5 — deprecation decision

- Only after parity and usage review, decide whether individual legacy routes redirect or remain specialist tools.
- Route deletion is outside X18.

## 10. Wireframes

The HTML wireframes are in [`X18_WIREFRAMES.html`](./X18_WIREFRAMES.html). They demonstrate:

- the investigation-led sidebar;
- Mission Control’s first viewport;
- Discovery → Entity handoff;
- the seven-question Operator layout;
- Level 1/2/3 progressive disclosure.

