# Claude Code Context - Flex (Pump.Fun Rug Detection System)

> **CRITICAL**: If Claude crashes or context is lost, read this file first for complete system understanding.

## Project Summary

**Flex** is a real-time monitoring system for Pump.Fun token migrations to PumpSwap. It detects rug pulls, identifies malicious creators, tracks funding networks, and calculates risk scores.

- **Language**: Python 3
- **Database**: SQLite (`pumpswap_tokens.db`)
- **Main Files**: `pumpfun_curve_listener.py`, `main.py`, `pump_fun_post_migration_analyzer.py`
- **Status**: ✅ Production Ready
- **Current Focus**: Domain system complete - Real-time domain capture for creators/funders

## Key Files & Their Purpose

### Core Listener & Analysis
| File | Purpose |
|------|---------|
| `pumpfun_curve_listener.py` | Main WebSocket listener, migration detection, real-time price tracking |
| `pump_fun_post_migration_analyzer.py` | Risk scoring engine, rug detection algorithm |
| `main.py` | Flask API + web UI dashboard |
| `realtime_creator_funding_extractor.py` | NEW: Real-time extraction when tokens launch |

### Supporting Scripts
| File | Purpose |
|------|---------|
| `scripts/analyze_creator_patterns.py` | Network analysis for coordinated funders |
| `utils/creator_blocklist_checker.py` | Checks if creator is on blocklist |
| `address_tags.py` | NEW: Persistent address metadata and domain tagging system |
| `domain_extraction.py` | NEW: Extract domain names from transaction metadata |
| `domain_mapping.py` | NEW: Persistent domain registry with bidirectional lookups |

### Database
| Table | Purpose |
|-------|---------|
| `token_analysis` | 105 tokens, analysis results, prices, risk scores |
| `creator_blocklist` | Known malicious creators, reputation scores |
| `creator_funders` | Funder-creator relationships (pre-migration SOL transfers) |
| `creator_networks` | Coordinated funding groups |

## Critical Concepts

### Pre-Migration Funding Model
**KEY INSIGHT**: Creators receive SOL **BEFORE** token launch as preparation. Once migrated, SOL transfers are just trading activity.

- **Pre-migration**: Large SOL transfers (20-150+ SOL/creator)
- **Post-migration**: Only trading/liquidity
- **Filter**: Query only signatures before token creation timestamp
- **Result**: 1,387x improvement (0.31 SOL → 700+ SOL)

### Creator Extraction (3-Method Hybrid)
1. **Metaplex Metadata** (15 tokens) - HIGH confidence
2. **DAS API Fresh Query** (1 token) - HIGH confidence
3. **Earliest Transaction Fallback** (89 tokens) - MEDIUM confidence
- **Coverage**: 100% (105/105 tokens)

### Risk Scoring
Calculates rug probability based on:
- Mint concentration (wallets holding >20%)
- Buy/sell patterns (suspicious dumps)
- Creator reputation (on blocklist?)
- Peak timing (quick peaks <30min = RED FLAG)
- Market cap progression

### Rug Detection Pattern
**"quick_peak_low_mc"** - Auto-flag creators:
- Peak reached in <30 minutes from migration
- Peak market cap <$100,000
- Often followed by rug pulls

## Latest Features (Feb 19-20, 2026)

### Cross-Funder Coordinator System
- **Status**: ✅ Complete
- **Purpose**: Identifies patterns where multiple funders collaborate to support token creators
- **Implementation**: Clustering algorithm detects coordinated funding networks
- **UI**: Super-Clusters page shows network details, memorable names, creator reuse metrics
- **Display**: Full addresses, flow visualization, network connectivity analysis

### Address Flow Visualization
- **Status**: ✅ Complete
- **Purpose**: Visual representation of SOL flows through funder networks
- **Location**: Networks modal in UI
- **Feature**: Shows all inbound/outbound transfers for coordinated accounts

### UI Enhancements
- ✅ Coordinator table with CEX/INFRA filtering toggle
- ✅ Clickable column sorting in token table
- ✅ Session complete summary dashboard
- ✅ Simplified analysis data display (tick/cross indicators)
- ✅ Color-coded badges matching theme (cyan/gray)

### Database & Clustering
- ✅ Creator reuse metrics - tracks how often same funders appear across different creators
- ✅ Super-cluster detection - identifies major funding networks
- ✅ Memorable network naming system - user-friendly identifiers instead of "Network_X"
- ✅ Full address display in modals (no truncation)

## Current Work: Continued Development

**Active Focus Areas**:
- Creator funding network analysis and visualization
- Cross-funder coordination detection
- Risk scoring integration with funder reputation
- CEX/INFRA wallet filtering and tagging
- Real-time funding extraction on token launch

## Database Schema (Key Tables)

### token_analysis
```sql
mint, symbol, created_at, final_creator_address,
market_cap_highest, market_cap_highest_at,
price_current, price_highest,
rug_probability, risk_level,
quick_peak_low_mc (0/1), analyzed_at
```

### creator_funders (NEW)
```sql
creator_address, funder_address, amount_sol,
first_detected_at, is_spam_dust
UNIQUE(creator_address, funder_address)
```

### creator_blocklist
```sql
creator_address, rug_count, reputation,
connected_to_malicious, network_members
```

## How to Continue After Context Loss

1. **Read this file first** for complete overview
2. **Check current status**: `grep -c "✓ Checked" /private/tmp/claude/.../tasks/bd82073.output`
3. **Review memories**: Available memories include:
   - `project_overview`
   - `creator_funding_extraction_status`
   - `extraction_integration_strategy`
   - `realtime_funding_extraction_implemented`

4. **Check git status**: `git status --short`
5. **Look at monitoring**:
   ```bash
   # Extraction progress
   tail -20 /private/tmp/claude/.../tasks/bd82073.output

   # Database status
   sqlite3 pumpswap_tokens.db "SELECT COUNT(*) FROM creator_funders;"
   ```

## Active Tasks & Status (Feb 20, 2026)

- ✅ Creator funding extraction complete
- ✅ Real-time integration complete
- ✅ Cross-funder coordinator system complete
- ✅ Address flow visualization complete
- ✅ Super-cluster detection and naming complete
- ✅ Creator reuse metrics complete
- ✅ UI coordinator table with filtering complete
- ⏳ Documentation cleanup (removing 300+ obsolete .md files)
- ⏳ Next phase: Integration improvements and performance optimization

## Running the System

### Start Main Listener
```bash
python3 pumpfun_curve_listener.py
# Detects token migrations, extracts creators, tracks prices
# Real-time funding extraction happens automatically
```

### Start Web UI
```bash
python3 main.py
# Flask server on http://localhost:5002
# Shows token analysis with sorting/filtering
```

### Monitor Real-Time Funding
```bash
tail -f listener.log | grep "REALTIME_FUNDING"
```

### Check Extraction Progress
```bash
tail -f /private/tmp/claude/.../tasks/bd82073.output | grep "✓ Checked"
```

## Common Issues & Solutions

### Issue: "creator_funders table empty"
- Extraction script may not save to DB automatically
- Solution: Run enhanced script: `/tmp/extract_creator_transfers_with_db_save.py`

### Issue: "Missing creator addresses"
- May need to run creator extraction again
- Check: `SELECT COUNT(DISTINCT final_creator_address) FROM token_analysis;`

### Issue: "RPC timeouts"
- System has fallback chains: Helius → QuickNode → Public Solana
- Check: Look for "[RPC]" logs with fallback indicators

## Performance Notes

- **Listener**: ~5% CPU, minimal memory (event-driven)
- **Real-time extraction per token**: 2-5 seconds (non-blocking)
- **Database queries**: <100ms (indexed on creator_address, funder_address)
- **UI loading**: <500ms (cached data)

## Next Phase Goals

1. Complete batch extraction (100/100 creators)
2. Run network analysis (find coordinated funders)
3. Integrate funder reputation into risk scoring
4. Update blocklist with suspicious funding patterns
5. Deploy real-time system to production

## Questions?

If context is lost:
1. Read this file (you're doing it!)
2. Check SYSTEM_WORKFLOW.md for detailed architecture
3. Review git commit history: `git log --oneline | head -20`
4. Check memory files in project for specific topics

---

**Last Updated**: 2026-02-20 14:00
**By**: Claude Code (Haiku 4.5)
**Status**: Complete & Production Ready
**Current Branch**: main (recovered from recover-feb19 on 2026-02-20)
**Note**: Removed dependency on 366 .md documentation files - using CLAUDE.md + memory system instead
