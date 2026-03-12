# FLEX Master Architecture — Quick Navigation

**Document**: `FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md` (2,420 lines, 84 KB)

This is the authoritative engineering reference for the FLEX Solana intelligence platform. Use this index to find what you need.

---

## By Role

### For Operators / DevOps
- **System Scheduling** (Section 8) — Daily pipeline at 5 AM UTC, 2-5 minute runtime
- **Deployment Architecture** (Section 10) — Hardware requirements, setup steps, monitoring
- **Appendix B** — Configuration reference (.env variables)
- **Appendix C** — Performance benchmarks

**Key Files to Know**:
- `dev_intelligence_detection.py` — Daily pipeline orchestrator
- `database/flex_complete_database.db` — Central data store (SQLite WAL)
- `/var/log/flex/dev_intelligence.log` — Pipeline logs

### For Data Analysts / SQL Users
- **Data Model and Database Schema** (Section 3) — All 15+ tables with relationships
- **Alert System** (Section 7) — Alert thresholds and generation rules
- **Appendix A** — SQL query examples (copy-paste ready)

**Key Tables**:
- `master_launch_signals` — Primary alert source (CRITICAL/HIGH/WATCH/LOW)
- `org_launch_windows` — Multi-window launch probabilities (24h, 72h, 7d)
- `org_snapshots` — Daily activity metrics for time-series analysis
- `org_risk_scores` — Risk assessment (rug probability, instability)

### For Backend Engineers / Python Developers
- **Full System Architecture** (Section 2) — Pipeline visualization with 6 phases
- **Core Algorithms** (Section 4) — All formulas with pseudocode
- **Core Python Classes** (Section 5) — 8 main classes with methods and dependencies

**Key Classes**:
- `DevIntelligenceEngine` (Phase 1) — Organization detection via graph clustering
- `LaunchProbabilityModel` (Phase 2) — 7-day launch prediction
- `DevIntelligenceV3Engine` (Phase 3) — Predictive analytics
- `MasterLaunchScoreEngine` (Phase 6) — Unified alert scoring

### For Frontend / Dashboard Developers
- **UI / Dashboard Architecture** (Section 9) — 6 proposed views with SQL and API endpoints
- **System Overview** (Section 1) — Intelligence model explanation

**Key Endpoints**:
- `GET /api/orgs/critical` — CRITICAL alert organizations
- `GET /api/orgs/watchlist` — HIGH + CRITICAL alerts
- `GET /api/orgs/<id>/history?days=30` — Time-series data

### For Product / Leadership
- **System Overview** (Section 1) — Purpose and intelligence model
- **Prediction Signals** (Section 6) — The 4 signal layers explained
- **Future Extensions** (Section 11) — Roadmap (ML, fingerprinting, cross-chain, trading)

---

## By Topic

### Understanding Alerts
1. **Section 1** — Intelligence model (why we can detect launches)
2. **Section 7** — Alert system (thresholds, rules, dedup logic)
3. **Appendix D** — Glossary (alert_level, CRITICAL, HIGH, WATCH, LOW)

### Understanding Signals
1. **Section 4** — Core algorithms (6 detailed algorithm breakdowns)
2. **Section 6** — Prediction signals (4 layers, 8 signals)
3. **Each algorithm includes**:
   - Input/output specification
   - Mathematical formula
   - Interpretation guide
   - Complexity analysis

### Understanding Data Flow
1. **Section 2** — Full pipeline (transfer_index → alerts)
2. **Section 3** — Data model (15+ tables, relationships, update frequency)
3. **Section 8** — System scheduling (when things run)

### Understanding Architecture
1. **Section 2** — Pipeline visualization
2. **Section 10** — Deployment architecture (components, flow diagram)
3. **Appendix C** — Performance benchmarks

### Understanding Code
1. **Section 5** — Core Python classes (8 classes, methods, dependencies)
2. **Section 2** — Pipeline phases (what each phase does)
3. **Each class description includes**:
   - File location
   - Responsibility
   - Key methods with signatures
   - Dependencies
   - Performance characteristics

---

## By Problem

**"I need to know how alerts are generated"**
→ Section 7 (Alert System) + Section 4, Algorithm 6 (Master Launch Score)

**"I need to query organizations by alert level"**
→ Section 3 (master_launch_signals table) + Appendix A (SQL Query 1)

**"I need to understand why an org got CRITICAL"**
→ Section 6 (Prediction Signals) + Section 9 (Explainability layer concept)

**"I need to fix a bug in probability calculation"**
→ Section 4, Algorithm 2 (Launch Probability) + Section 5 (LaunchProbabilityModel class)

**"I need to deploy FLEX to production"**
→ Section 10 (Deployment Architecture) + System Scheduling (Section 8)

**"I need to add a new signal"**
→ Section 6 (Prediction Signals) + Section 4 (Algorithm structure) + Section 5 (class pattern)

**"I need to understand seed concentration"**
→ Section 4, Algorithm 3 (Seed Concentration) + Section 3 (creator_seed_metrics table)

**"I need to visualize organization activity"**
→ Section 9 (Dashboard Views) + Appendix A (SQL queries)

**"I need to improve alert accuracy"**
→ Section 11, Extension 11 (Feedback Loop & Model Improvement)

---

## Key Numbers at a Glance

| Metric | Value |
|--------|-------|
| **Phases in Pipeline** | 6 (Organization Detection → Master Score) |
| **Prediction Signals** | 8 (launch_probability, launch_wave, seed_concentration, funder_overlap, organization_momentum, creator_reuse, operator_activity, reputation) |
| **Signal Layers** | 4 (Structural, Behavioral, Preparation, Predictive) |
| **Alert Levels** | 4 (CRITICAL ≥0.75, HIGH 0.60-0.74, WATCH 0.40-0.59, LOW <0.40) |
| **Database Tables** | 15+ (transfer_index, dev_organizations, org_launch_windows, org_snapshots, org_risk_scores, creator_seed_metrics, funder_overlap, launch_waves, master_launch_signals, dev_reputation, token_outcome_predictions, and more) |
| **Python Classes** | 8 (TransferIndexer, DevIntelligenceEngine, LaunchProbabilityModel, DevIntelligenceV3Engine, CreatorSeedMetricsAnalyzer, FunderOverlapAnalyzer, LaunchWaveDetectionEngine, MasterLaunchScoreEngine) |
| **Daily Pipeline Runtime** | 2-5 minutes |
| **Query Latency (P99)** | <5ms |
| **RPC Savings** | 98% (via transfer indexing) |

---

## Document Sections at a Glance

| Section | Topic | Audience | Length |
|---------|-------|----------|--------|
| 1 | System Overview | Everyone | 500 lines |
| 2 | Full Pipeline | Engineers, Operators | 600 lines |
| 3 | Database Schema | Engineers, Analysts | 800 lines |
| 4 | Core Algorithms | Engineers, Analysts | 700 lines |
| 5 | Python Classes | Engineers | 600 lines |
| 6 | Prediction Signals | Engineers, Product | 500 lines |
| 7 | Alert System | Everyone | 400 lines |
| 8 | System Scheduling | Operators, Engineers | 300 lines |
| 9 | UI/Dashboard | Frontend, Product | 400 lines |
| 10 | Deployment | Operators, DevOps | 500 lines |
| 11 | Future Extensions | Everyone | 700 lines |
| A | SQL Reference | Analysts, Engineers | 200 lines |
| B | Configuration | Operators | 50 lines |
| C | Benchmarks | Everyone | 30 lines |
| D | Glossary | Everyone | 80 lines |

---

## Critical Sections to Read First

**Minimum Understanding (30 minutes)**:
1. Section 1 — System Overview (understand the problem)
2. Section 2 — Full Pipeline (understand the flow)
3. Section 7 — Alert System (understand the output)

**Operational Understanding (2 hours)**:
- Above + Section 10 (how to deploy)
- Section 8 (when things run)
- Appendix B (configuration)

**Engineering Understanding (4 hours)**:
- Above + Section 3 (database schema)
- Section 4 (core algorithms)
- Section 5 (Python classes)

**Complete Understanding (8 hours)**:
- Read entire document
- Study code in `src/core/`
- Run example queries from Appendix A

---

## File Locations Reference

```
Root
├── src/core/
│   ├── transfer_indexer.py              (Phase 0)
│   ├── dev_intelligence_graph.py        (Phase 1)
│   ├── dev_intelligence_v2.py           (Phase 2)
│   ├── dev_intelligence_v3.py           (Phase 3)
│   ├── creator_seed_metrics.py          (Phase 4)
│   ├── funder_overlap_analysis.py       (Phase 4.5)
│   ├── launch_wave_detection.py         (Phase 5)
│   ├── master_launch_score.py           (Phase 6)
│   ├── dev_intelligence_api.py          (REST API)
│   └── main.py                          (Flask app)
│
├── database/
│   ├── flex_complete_database.db        (SQLite)
│   └── migrations/
│       ├── phase3_transfer_index_migration.sql
│       ├── dev_intelligence_graph.sql
│       ├── dev_intelligence_v2.sql
│       ├── dev_intelligence_v3.sql
│       ├── creator_seed_metrics.sql
│       ├── funder_overlap_signal.sql
│       ├── launch_wave_detection.sql
│       └── master_launch_score.sql
│
├── dev_intelligence_detection.py        (Daily pipeline orchestrator)
│
└── docs/
    ├── FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md  ← YOU ARE HERE
    └── [other documentation]
```

---

## Related Documentation

**Quick References**:
- `MASTER_LAUNCH_SCORE_QUICK_REFERENCE.md` — One-page score formula
- `COMPLETE_SIGNAL_ARCHITECTURE.md` — All 8 signals at a glance
- `FUNDER_OVERLAP_SUMMARY.md` — Phase 4.5 explained

**Implementation Guides**:
- `MASTER_LAUNCH_SCORE_IMPLEMENTATION.md` — Phase 6 deep dive
- `DELIVERY_SUMMARY_MLS.md` — Master Launch Score delivery checklist

**Executive Summaries**:
- `MASTER_LAUNCH_SCORE_SUMMARY.md` — Problem → Solution narrative
- `EXECUTIVE_SUMMARY.md` — High-level overview

---

## How to Use This Document

**For One-Off Queries**: Use Ctrl+F to search for keywords
- `ALTER TABLE` → All table modifications
- `CREATE INDEX` → All indexes
- `def ` + method name → Class method signature
- `ALTER` + phase name → Phase-specific changes

**For Deep Understanding**: Read sections linearly
- Sections 1-2 provide context
- Sections 3-6 are complementary (can read in any order)
- Sections 7-11 build on understanding of 1-6

**For Implementation**: Use Section 5 (Classes) as your map
- Each class tells you what file to look at
- Each method tells you what queries to run
- Each dependency tells you what upstream data you need

**For Troubleshooting**: Go directly to relevant section
- Alert issue → Section 7
- Data issue → Section 3 + Appendix A
- Code issue → Section 5 + Section 4
- Performance issue → Appendix C

---

**Document Location**: `/Users/kevinkeaveney/Dev/claude/flex/docs/FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md`

**Last Updated**: March 2026

**Maintained By**: FLEX Development Team

**Next Review**: June 2026
