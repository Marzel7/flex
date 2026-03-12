# FLEX Project Structure

**Clean, organized project layout with clear separation of concerns**

---

## Root Directory (Production Focus)

```
/
├── dev_intelligence_detection.py      ⭐ Main daily pipeline (5 AM UTC)
├── run.py                             ⭐ Application entry point
├── .env                               Configuration variables
├── .gitignore                         Git ignore rules
├── requirements.txt                   Python dependencies
├── setup.py                           Package setup
├── README.md                          Project README
└── PROJECT_STRUCTURE.md               This file
```

**Philosophy**: Only essential production files in root. Everything else organized logically.

---

## Core Modules (Production Code)

```
src/core/                              Core intelligence modules
├── __init__.py
├── transfer_indexer.py               Phase 0: Transfer indexing
├── dev_intelligence_graph.py         Phase 1: Organization detection
├── dev_intelligence_v2.py            Phase 2: Launch probability
├── dev_intelligence_v3.py            Phase 3: Predictive analytics
├── creator_seed_metrics.py           Phase 4: Seed concentration
├── funder_overlap_analysis.py        Phase 4.5: Funder overlap
├── launch_wave_detection.py          Phase 5: Launch waves
├── master_launch_score.py            Phase 6: Master score
├── dev_intelligence_api.py           REST API endpoints
├── main.py                           Flask application
└── [other modules]
```

**Purpose**: All core intelligence pipeline logic

---

## Utility Scripts

```
src/scripts/                           Helper scripts
├── helius_webhook_sync.py            Webhook synchronization
├── cleanup_transfers.py               Transfer cleanup utility
├── cluster_detection.py               Cluster detection utility
├── graph_dev_farm_detection.py       Farm detection utility
├── advanced_farm_intelligence_detection.py
└── launch_prediction_detection.py
```

**Purpose**: Standalone utilities, not part of main pipeline

---

## Tests

```
tests/                                 Test suite (40+ tests)
├── __init__.py
├── conftest.py                       Pytest configuration
├── test_incremental_extraction.py
├── test_phase1_monitoring.py
├── test_phase3_optimizations.py
├── test_v2_integration.py
├── test_helius_endpoint.py
├── [40+ more test files]
└── [test data/fixtures]
```

**Purpose**: All test code and fixtures

**Run**: `pytest tests/`

---

## Database

```
database/                              SQLite data and migrations
├── flex_complete_database.db         Production database
├── migrations/                        Schema migrations
│   ├── dev_intelligence_graph.sql
│   ├── dev_intelligence_v2.sql
│   ├── dev_intelligence_v3.sql
│   ├── creator_seed_metrics.sql
│   ├── funder_overlap_signal.sql
│   ├── launch_wave_detection.sql
│   ├── master_launch_score.sql
│   └── [other migrations]
└── [backups]
```

**Purpose**: Data persistence and schema definitions

---

## Documentation

```
docs/                                  Documentation
├── master/                            Master technical architecture
│   ├── README.md                     Quick start guide
│   ├── FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md
│   ├── MASTER_ARCHITECTURE_INDEX.md
│   └── DOCUMENTATION_COMPLETE.md
├── [other documentation]
└── [quick references]
```

**Purpose**: Complete technical and operational documentation

---

## Archive

```
archive/                               Legacy/deprecated code
├── phase1_anomaly_detection.py       Old monitoring
├── phase1_monitoring_dashboard.py    Old monitoring
├── phase1_monitoring_enhanced.py     Old monitoring
└── phase2_3_deployment_verification.py
```

**Purpose**: Preserve historical code, not used in production

---

## Logs

```
logs/                                  Application logs
├── dev_intelligence.log              Main pipeline log
└── [other logs]
```

**Purpose**: Runtime log files

---

## Configuration Files

```
Root level:
├── .env                               Environment variables
├── .env.example                       Example env file
├── .gitignore                         Git ignore rules
├── .github/                           GitHub workflows
├── requirements.txt                   Python dependencies
├── setup.py                           Package setup
└── pyproject.toml                     Project metadata
```

---

## Directory Tree Summary

```
FLEX/
├── ⭐ dev_intelligence_detection.py   Main pipeline
├── ⭐ run.py                          Entry point
├── database/
│   ├── flex_complete_database.db
│   └── migrations/
├── docs/
│   ├── master/                        📚 Master architecture
│   └── [other docs]
├── src/
│   ├── core/                          🔧 Core modules
│   ├── scripts/                       🛠️  Utilities
│   └── __init__.py
├── tests/                             🧪 Test suite
├── archive/                           📦 Legacy code
├── logs/                              📋 Log files
├── .env                               Configuration
├── .gitignore
├── requirements.txt
├── setup.py
└── README.md
```

---

## File Statistics

| Location | Files | Type | Purpose |
|----------|-------|------|---------|
| Root | 2 | .py | Production code |
| src/core/ | 20+ | .py | Core modules |
| src/scripts/ | 6 | .py | Utilities |
| tests/ | 44+ | .py | Test suite |
| archive/ | 4 | .py | Legacy code |
| database/ | 8 | .sql | Migrations |
| docs/master/ | 4 | .md | Documentation |
| **Total** | **~100** | Mixed | Complete system |

---

## Usage Guide

### Run Main Pipeline
```bash
python3 dev_intelligence_detection.py
```

### Run Application
```bash
python3 run.py
```

### Run a Utility Script
```bash
python3 src/scripts/helius_webhook_sync.py
```

### Run Tests
```bash
pytest tests/
pytest tests/test_v2_integration.py -v
```

### Access Documentation
```
docs/master/README.md                  Start here
docs/master/MASTER_ARCHITECTURE_INDEX.md  Navigation guide
docs/master/FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md  Full reference
```

---

## Development Workflow

### Adding a New Feature
1. Create code in `src/core/` if it's core logic
2. Create code in `src/scripts/` if it's a utility
3. Add tests in `tests/`
4. Update documentation in `docs/`
5. Commit with clear message
6. Test with `pytest tests/`

### Debugging
1. Check logs in `logs/dev_intelligence.log`
2. Run failing test: `pytest tests/test_xxx.py -v`
3. Check database: `sqlite3 database/flex_complete_database.db`

### Deployment
1. Ensure `dev_intelligence_detection.py` is executable
2. Set `run.py` as entry point if using application server
3. Configure `.env` for production
4. Set up cron: `0 5 * * * cd /path/to/flex && python3 dev_intelligence_detection.py`

---

## Key Files to Know

**Production Critical**:
- `dev_intelligence_detection.py` - Daily pipeline orchestrator
- `src/core/master_launch_score.py` - Alert scoring engine
- `database/flex_complete_database.db` - Central data store

**Entry Points**:
- `run.py` - Application server
- `dev_intelligence_detection.py` - Batch pipeline

**Configuration**:
- `.env` - Environment variables
- `requirements.txt` - Dependencies
- Database migrations in `database/migrations/`

**Documentation**:
- `docs/master/FLEX_MASTER_TECHNICAL_ARCHITECTURE_COMPLETE.md` - Full reference
- `docs/master/README.md` - Quick start

---

## Best Practices

✅ Keep root directory minimal (production focus)
✅ Put utilities in `src/scripts/`
✅ Put tests in `tests/`
✅ Put legacy code in `archive/`
✅ Keep configuration in `.env`
✅ Document in `docs/`
✅ Use clear git commit messages
✅ Run tests before committing

---

## Version

**Last Updated**: March 12, 2026
**FLEX Version**: 3.1
**Project Status**: Production Ready

---

## Questions?

See `docs/master/MASTER_ARCHITECTURE_INDEX.md` for comprehensive navigation guide.
