# FLEX Master Technical Architecture

**Complete engineering reference for the FLEX Solana intelligence platform**

---

## Overview

This folder contains the authoritative technical documentation for FLEX, a Solana blockchain intelligence system that detects developer organizations and predicts token launches.

---

## Files in This Folder

### 1. **FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md** (2,420 lines, 84 KB)

The primary reference document containing 11 comprehensive sections:

1. **System Overview** — Purpose, intelligence model, core components
2. **Full System Architecture** — 6-phase pipeline with data flow
3. **Data Model and Database Schema** — All 15+ tables, relationships, indexes
4. **Core Algorithms** — 6 algorithms with formulas and explanations
5. **Core Python Classes** — 8 classes with methods and dependencies
6. **Prediction Signals** — 4 layers, 8 signals, interaction effects
7. **Alert System** — Generation, thresholds, deduplication
8. **System Scheduling** — Daily pipeline, real-time ingestion
9. **UI / Dashboard Architecture** — 6 views with SQL queries
10. **Deployment Architecture** — Hardware, software, procedures
11. **Future Extensions** — 11 planned improvements

Plus 4 appendices:
- Appendix A: SQL Query Reference (15+ queries)
- Appendix B: Configuration Reference
- Appendix C: Performance Benchmarks
- Appendix D: Glossary (25+ terms)

**Read this for**: Complete technical understanding

---

### 2. **MASTER_ARCHITECTURE_INDEX.md** (277 lines)

Navigation guide organized by:
- **By Role** — Different sections for operators, analysts, engineers, frontend devs, product
- **By Topic** — Access grouped by subject (alerts, signals, data flow, code, architecture)
- **By Problem** — Find what you need to solve a specific problem
- **Key Numbers** — Reference metrics at a glance
- **How to Use** — Guidance for different reading patterns

**Read this for**: Quick navigation to what you need

---

### 3. **DOCUMENTATION_COMPLETE.md** (406 lines)

Summary of what was created:
- Deliverable overview
- Documentation statistics
- Coverage summary (technical, implementation, design)
- Audiences served
- Key design decisions documented
- Usage guidance
- Quality checklist
- Next steps for different roles

**Read this for**: Understanding what documentation exists and how to use it

---

## Quick Start

### I'm New to FLEX

1. Start with `MASTER_ARCHITECTURE_INDEX.md`
2. Read Section 1 of `FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md` (System Overview)
3. Read Section 2 (Full Pipeline)
4. Pick a section based on your role

### I Need to Operate FLEX

1. `MASTER_ARCHITECTURE_INDEX.md` → Find "For Operators / DevOps"
2. Read Section 10 (Deployment Architecture)
3. Read Section 8 (System Scheduling)
4. Refer to Appendix B (Configuration)

### I Need to Modify FLEX

1. Find the relevant algorithm in Section 4
2. Understand the data model in Section 3
3. Locate the class in Section 5
4. Check dependencies in Section 5
5. Review relevant code in `src/core/`

### I Need to Query FLEX Data

1. Section 3 (Database Schema) for table definitions
2. Appendix A (SQL Examples) for copy-paste queries

### I Need to Understand a Specific Topic

1. Use `MASTER_ARCHITECTURE_INDEX.md` to find the relevant section
2. Use Ctrl+F in `FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md` to search

---

## Key Documents at a Glance

| Document | Lines | Size | Purpose |
|----------|-------|------|---------|
| **FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md** | 2,420 | 84 KB | Complete technical reference |
| **MASTER_ARCHITECTURE_INDEX.md** | 277 | 12 KB | Navigation guide |
| **DOCUMENTATION_COMPLETE.md** | 406 | 18 KB | Summary and index |

---

## System in 30 Seconds

FLEX analyzes Solana blockchain transfers to detect developer organizations and predict token launches.

**Pipeline**: Transfer Indexing → Organization Detection → Launch Probability → Predictive Analytics → Seed Metrics → Funder Overlap → Launch Waves → **Master Launch Score**

**Output**: Alert levels (CRITICAL/HIGH/WATCH/LOW) based on 8 prediction signals

**Performance**: 2-5 minutes daily, <5ms query latency, 98% RPC savings

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Pipeline Phases | 6 |
| Prediction Signals | 8 |
| Signal Layers | 4 |
| Alert Levels | 4 |
| Database Tables | 15+ |
| Python Classes | 8 |
| Daily Runtime | 2-5 minutes |
| Query Latency | <5ms |
| RPC Savings | 98% |

---

## Documentation Audience Map

**For Different Roles**:
- **Operators/DevOps** → Section 10, 8, Appendix B
- **Data Analysts** → Section 3, 7, Appendix A
- **Backend Engineers** → Section 2, 4, 5
- **Frontend Developers** → Section 9
- **Product/Leadership** → Section 1, 6, 11

---

## How Sections Connect

```
Section 1: Overview
    ↓
Section 2: Full Pipeline
    ↓
Sections 3-6: Details (Database, Algorithms, Classes, Signals)
    ↓
Sections 7-10: Operations (Alerts, Scheduling, UI, Deployment)
    ↓
Section 11: Future Extensions
```

---

## Related Documentation

Outside this folder (in `/docs/`):

- `MASTER_LAUNCH_SCORE_QUICK_REFERENCE.md` — One-page formula
- `MASTER_LAUNCH_SCORE_IMPLEMENTATION.md` — Phase 6 deep dive
- `COMPLETE_SIGNAL_ARCHITECTURE.md` — All 8 signals
- `FUNDER_OVERLAP_SUMMARY.md` — Phase 4.5 explained
- `DELIVERY_SUMMARY_MLS.md` — Delivery checklist

---

## Code Reference

All code locations are documented in the master architecture.

**Key Files**:
- `dev_intelligence_detection.py` — Daily pipeline orchestrator
- `src/core/` — All 8 phase modules
- `database/flex_complete_database.db` — SQLite database
- `database/migrations/` — Schema migrations

---

## Status

✅ **COMPLETE AND PRODUCTION READY**

- 11 sections covering entire FLEX system
- 4 appendices with practical reference material
- 2,420 lines of comprehensive documentation
- Suitable for engineers, operators, analysts, product team

---

## Version

**Documentation Version**: 3.1
**FLEX System Version**: 3.1
**Created**: March 12, 2026
**Last Updated**: March 12, 2026
**Next Review**: June 2026

---

## Where to Start

**Never read documentation before?**
→ `MASTER_ARCHITECTURE_INDEX.md` (5 min) then Section 1 of main doc (20 min)

**Need to do something specific?**
→ `MASTER_ARCHITECTURE_INDEX.md` - "By Problem" section

**Want complete understanding?**
→ Read `FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md` sections 1-2, then jump to what interests you

**Need to deploy?**
→ Section 10 (Deployment Architecture) of main doc

**Need to understand data?**
→ Section 3 (Database Schema) + Appendix A (SQL Examples)

---

**Start Here**: Open `MASTER_ARCHITECTURE_INDEX.md` for guided navigation
