# Webhook Documentation Index

**Complete Reference Guide for FLEX Webhook System**

---

## 📚 Documentation Overview

The webhook system has comprehensive documentation organized by use case and depth:

### Quick Start (5 minutes)
- **[WEBHOOK_START_HERE.md](WEBHOOK_START_HERE.md)** - Begin here! Simple step-by-step
- **[WEBHOOK_RANKING_QUICK_START.md](WEBHOOK_RANKING_QUICK_START.md)** - Creator ranking reference

### Integration & Setup (10-15 minutes)
- **[WEBHOOK_INTEGRATION_GUIDE.md](WEBHOOK_INTEGRATION_GUIDE.md)** - Installation & configuration
- **[WEBHOOK_INTEGRATION_COMPLETE.md](WEBHOOK_INTEGRATION_COMPLETE.md)** - Full integration overview
- **[WEBHOOK_INTEGRATION_SUMMARY.txt](WEBHOOK_INTEGRATION_SUMMARY.txt)** - Checklist format

### Technical Deep Dive (30+ minutes)
- **[WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md)** ⭐ **← READ THIS** - How creators flow through the system with code references
- **[WEBHOOK_DATABASE_SCHEMA.md](WEBHOOK_DATABASE_SCHEMA.md)** ⭐ **← AND THIS** - Complete database schema with SQL
- **[WEBHOOK_ARCHITECTURE_M5.md](WEBHOOK_ARCHITECTURE_M5.md)** - Full architecture details
- **[WEBHOOK_CREATOR_RANKING_GUIDE.md](WEBHOOK_CREATOR_RANKING_GUIDE.md)** - Risk scoring explained

### Reference
- **[WEBHOOK_M5_SUMMARY.txt](WEBHOOK_M5_SUMMARY.txt)** - Overview & verification
- **[WEBHOOK_DEPLOYMENT_CHECKLIST.md](WEBHOOK_DEPLOYMENT_CHECKLIST.md)** - Deployment steps

---

## 🎯 Pick Your Path

### "I just want to get it working"
1. Read: [WEBHOOK_START_HERE.md](WEBHOOK_START_HERE.md)
2. Run: `python3 main.py`
3. Test: `curl http://localhost:5002/api/webhook/status`
4. Done! ✅

### "I need to understand the system"
1. Read: [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md) - Understand the 3-stage flow
2. Read: [WEBHOOK_DATABASE_SCHEMA.md](WEBHOOK_DATABASE_SCHEMA.md) - Learn the database tables
3. Read: [WEBHOOK_CREATOR_RANKING_GUIDE.md](WEBHOOK_CREATOR_RANKING_GUIDE.md) - Understand scoring
4. You now understand everything! 🎓

### "I need to customize or debug"
1. Skim: [WEBHOOK_ARCHITECTURE_M5.md](WEBHOOK_ARCHITECTURE_M5.md) - Architecture overview
2. Reference: [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md) - Code line numbers
3. Reference: [WEBHOOK_DATABASE_SCHEMA.md](WEBHOOK_DATABASE_SCHEMA.md) - SQL queries
4. Edit files with understanding 🔧

### "I need to deploy to production"
1. Check: [WEBHOOK_DEPLOYMENT_CHECKLIST.md](WEBHOOK_DEPLOYMENT_CHECKLIST.md) - All steps
2. Review: [WEBHOOK_INTEGRATION_COMPLETE.md](WEBHOOK_INTEGRATION_COMPLETE.md) - Full integration
3. Test: [WEBHOOK_INTEGRATION_GUIDE.md](WEBHOOK_INTEGRATION_GUIDE.md) - Test scenarios
4. Deploy with confidence 🚀

---

## 📖 Document Structure

### WEBHOOK_CREATOR_DATA_FLOW.md ⭐

**Best for**: Understanding how data flows through the system

**Contains**:
- Architecture diagram (3 stages)
- SQL schema for 3 core tables
- Code references with line numbers
- Complete request/response flow
- Activity scoring logic
- 10 database queries executed per API call

**Key Sections**:
- Overview & Architecture Diagram
- Database Schema (with examples)
- Code References (all 3 stages)
  - Stage 1: Webhook Ingestion
  - Stage 2: Priority Worker Processing
  - Stage 3: Enrichment & Serving
- Activity Scoring Details
- Complete Request/Response Flow

**Example**: How does a creator get served to an API endpoint?
→ Answer is fully explained with code references

---

### WEBHOOK_DATABASE_SCHEMA.md ⭐

**Best for**: Understanding database design

**Contains**:
- Visual schema diagram
- 3 core tables (detailed)
- 7 integrated existing tables
- Sample rows (JSON)
- Common queries (SQL)
- Data volume examples
- Maintenance procedures

**Key Sections**:
- Schema Overview (diagram)
- Core Tables (3):
  - sol_transfers (deduplicated transfers)
  - address_activity (rolling statistics)
  - work_queue (priority queue)
- Integrated Tables (7):
  - creator_self_funding
  - creator_funders
  - token_analysis
  - creator_tags
  - coordinated_creator_edges
  - creator_to_creator_networks
  - funding_chains
- Common Queries
- Data Volume Examples
- Backup & Recovery
- Maintenance

**Example**: What columns exist in address_activity?
→ All columns listed with descriptions and examples

---

### WEBHOOK_ARCHITECTURE_M5.md

**Best for**: Complete technical reference

**Contains**:
- Full architecture explanation
- Data flow diagrams
- All 5 source files detailed
- RPC guardrails explained
- Performance characteristics
- Integration guide
- Logging examples
- Troubleshooting

**Key Sections**:
- Overview
- Architecture (4 components)
- Data Flow (2 stages)
- Database Schema (3 tables)
- Priority Scoring
- RPC Guardrails
- Integration Steps
- Performance Targets
- Logging
- Testing
- Troubleshooting

**Example**: What are the RPC guardrails?
→ Full explanation with code

---

### WEBHOOK_CREATOR_RANKING_GUIDE.md

**Best for**: Understanding risk scoring

**Contains**:
- Risk scoring overview
- 6 scoring components
- Scoring weights & thresholds
- 4 risk levels
- Component-based transparency
- Endpoint examples
- Customization guide

**Key Sections**:
- Risk Scoring Overview
- 6 Components:
  1. Activity Scoring
  2. Self-Funding Detection
  3. Distribution Pattern Analysis
  4. Concentration Risk
  5. Network Membership
  6. Token Behavior
- Risk Levels (critical/elevated/moderate/low)
- Customization
- Examples

**Example**: How is a creator's risk score calculated?
→ Each of 6 components explained with weights

---

### WEBHOOK_INTEGRATION_GUIDE.md

**Best for**: Quick 5-minute setup

**Contains**:
- Step-by-step installation
- Environment variables
- Test commands
- Architecture quick reference
- Key features checklist
- Files modified

**Key Sections**:
- Copy Files
- Update main.py
- Restart Flask
- Test Endpoints
- Environment Variables (Optional)
- Test Commands
- Logs to Check
- Architecture Quick Reference
- Customization

**Example**: How do I set up the webhook system?
→ 3 simple steps (copy, edit, restart)

---

### WEBHOOK_START_HERE.md

**Best for**: Getting started immediately

**Contains**:
- Just start Flask (no setup required!)
- New endpoints (ready to use)
- What's happening behind scenes
- How to test
- Key features
- Monitoring
- Documentation links

**Key Sections**:
- Just Start Flask
- New Endpoints
- What's Running
- Performance
- Files Created
- To Get Started (3 steps)
- Monitoring
- Configure Helius (optional)
- Risk Scores Explained
- Response Examples
- Troubleshooting
- Summary

**Example**: How do I start using the webhook system?
→ Just run `python3 main.py`

---

## 🔍 Finding Information

### "How do I...?"

**...set up the system?**
→ [WEBHOOK_START_HERE.md](WEBHOOK_START_HERE.md) or [WEBHOOK_INTEGRATION_GUIDE.md](WEBHOOK_INTEGRATION_GUIDE.md)

**...understand the data flow?**
→ [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md)

**...understand the database?**
→ [WEBHOOK_DATABASE_SCHEMA.md](WEBHOOK_DATABASE_SCHEMA.md)

**...customize the system?**
→ [WEBHOOK_CREATOR_RANKING_GUIDE.md](WEBHOOK_CREATOR_RANKING_GUIDE.md)

**...deploy to production?**
→ [WEBHOOK_DEPLOYMENT_CHECKLIST.md](WEBHOOK_DEPLOYMENT_CHECKLIST.md)

**...fix a problem?**
→ [WEBHOOK_ARCHITECTURE_M5.md](WEBHOOK_ARCHITECTURE_M5.md) (Troubleshooting section)

**...understand the risk scoring?**
→ [WEBHOOK_CREATOR_RANKING_GUIDE.md](WEBHOOK_CREATOR_RANKING_GUIDE.md)

**...see code examples?**
→ [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md) (has all code references with line numbers)

**...see SQL examples?**
→ [WEBHOOK_DATABASE_SCHEMA.md](WEBHOOK_DATABASE_SCHEMA.md) (has all common queries)

---

## 📊 Document Quick Stats

| Document | Length | Focus | Time to Read |
|----------|--------|-------|--------------|
| WEBHOOK_START_HERE.md | 3 KB | Quick start | 5 min |
| WEBHOOK_INTEGRATION_GUIDE.md | 6 KB | Setup | 5 min |
| WEBHOOK_CREATOR_RANKING_QUICK_START.md | 5 KB | Ranking reference | 5 min |
| WEBHOOK_INTEGRATION_COMPLETE.md | 10 KB | Full integration | 10 min |
| WEBHOOK_M5_SUMMARY.txt | 15 KB | Overview | 15 min |
| WEBHOOK_CREATOR_DATA_FLOW.md | 20 KB | **Data flow + code** | 20 min |
| WEBHOOK_DATABASE_SCHEMA.md | 18 KB | **Database design** | 20 min |
| WEBHOOK_ARCHITECTURE_M5.md | 15 KB | Technical reference | 30 min |
| WEBHOOK_CREATOR_RANKING_GUIDE.md | 12 KB | Risk scoring | 15 min |
| WEBHOOK_DEPLOYMENT_CHECKLIST.md | 8 KB | Deployment | 10 min |

---

## 🎓 Learning Paths

### Path 1: Get It Working (15 minutes)
1. WEBHOOK_START_HERE.md (5 min)
2. Run `python3 main.py` (2 min)
3. Test endpoints (3 min)
4. Read monitoring section (5 min)

### Path 2: Understand System (45 minutes)
1. WEBHOOK_START_HERE.md (5 min)
2. WEBHOOK_CREATOR_DATA_FLOW.md (20 min)
3. WEBHOOK_DATABASE_SCHEMA.md (20 min)

### Path 3: Customize & Extend (90 minutes)
1. Path 2 above (45 min)
2. WEBHOOK_CREATOR_RANKING_GUIDE.md (15 min)
3. WEBHOOK_ARCHITECTURE_M5.md (30 min)

### Path 4: Deploy to Production (60 minutes)
1. WEBHOOK_INTEGRATION_GUIDE.md (10 min)
2. WEBHOOK_INTEGRATION_COMPLETE.md (15 min)
3. WEBHOOK_DEPLOYMENT_CHECKLIST.md (20 min)
4. WEBHOOK_CREATOR_DATA_FLOW.md (references) (15 min)

---

## 🔗 Code File References

### webhook_handler.py (Transfer extraction & ingestion)
- Extract transfers: Lines 118-160
- Store transfers: Lines 162-185
- Update activity stats: Lines 187-235
- Enqueue addresses: Lines 237-260

**Reference**: [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md#stage-1-webhook-ingestion)

### webhook_worker.py (Priority processing)
- Fetch work: Lines 211-256
- Compute priority: Lines 53-210
- Process item: Lines 257-355
- Main loop: Lines 357-400

**Reference**: [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md#stage-2-priority-worker)

### webhook_creator_ranker.py (Risk scoring)
- Activity scoring: Lines 84-140
- Self-funding scoring: Lines 142-180
- Distribution scoring: Lines 182-220
- Compute overall: Lines 300-400
- Enrich creator: Lines 400-500

**Reference**: [WEBHOOK_CREATOR_RANKING_GUIDE.md](WEBHOOK_CREATOR_RANKING_GUIDE.md)

### webhook_api_enriched.py (Serving creators)
- Recent checks: Lines 29-182
- Top risk: Lines 188-207
- Risk details: Lines 210-309

**Reference**: [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md#stage-3-enrichment--serving)

### main.py (Flask integration)
- Webhook imports: Lines 24-30
- Webhook init: Lines 46-52

**Reference**: [WEBHOOK_INTEGRATION_GUIDE.md](WEBHOOK_INTEGRATION_GUIDE.md)

---

## ✅ Verification Checklist

Before going to production, make sure you've:

**Setup**:
- ✅ Read WEBHOOK_START_HERE.md
- ✅ Run Flask without errors
- ✅ Test /api/webhook/status endpoint

**Understanding**:
- ✅ Read WEBHOOK_CREATOR_DATA_FLOW.md
- ✅ Read WEBHOOK_DATABASE_SCHEMA.md
- ✅ Understand the 3 stages of data flow

**Configuration**:
- ✅ Read WEBHOOK_INTEGRATION_GUIDE.md
- ✅ Set HELIUS_WEBHOOK_AUTH if needed
- ✅ Verify database tables created

**Testing**:
- ✅ Send test webhook
- ✅ Check sol_transfers table
- ✅ Check API endpoints
- ✅ Monitor logs

**Deployment**:
- ✅ Read WEBHOOK_DEPLOYMENT_CHECKLIST.md
- ✅ Follow all deployment steps
- ✅ Run verification tests
- ✅ Monitor in production

---

## 🎯 Document Purposes

| Document | Primary Purpose |
|----------|-----------------|
| **WEBHOOK_START_HERE.md** | Get started immediately |
| **WEBHOOK_CREATOR_DATA_FLOW.md** | Understand data flow & code |
| **WEBHOOK_DATABASE_SCHEMA.md** | Understand database design |
| **WEBHOOK_ARCHITECTURE_M5.md** | Complete technical reference |
| **WEBHOOK_INTEGRATION_GUIDE.md** | Quick setup guide |
| **WEBHOOK_INTEGRATION_COMPLETE.md** | Full integration overview |
| **WEBHOOK_CREATOR_RANKING_GUIDE.md** | Risk scoring details |
| **WEBHOOK_RANKING_QUICK_START.md** | Ranking quick reference |
| **WEBHOOK_M5_SUMMARY.txt** | Implementation overview |
| **WEBHOOK_DEPLOYMENT_CHECKLIST.md** | Production deployment |

---

## 📋 Table of Contents by Topic

### Getting Started
- WEBHOOK_START_HERE.md
- WEBHOOK_INTEGRATION_GUIDE.md

### Understanding the System
- WEBHOOK_CREATOR_DATA_FLOW.md
- WEBHOOK_DATABASE_SCHEMA.md
- WEBHOOK_ARCHITECTURE_M5.md

### Risk Scoring
- WEBHOOK_CREATOR_RANKING_GUIDE.md
- WEBHOOK_RANKING_QUICK_START.md

### Integration & Setup
- WEBHOOK_INTEGRATION_GUIDE.md
- WEBHOOK_INTEGRATION_COMPLETE.md

### Deployment
- WEBHOOK_DEPLOYMENT_CHECKLIST.md
- WEBHOOK_INTEGRATION_SUMMARY.txt

### Reference
- WEBHOOK_M5_SUMMARY.txt
- WEBHOOK_ARCHITECTURE_M5.md

---

## 🚀 Next Steps

1. **Just want to use it?**
   → Start with [WEBHOOK_START_HERE.md](WEBHOOK_START_HERE.md)

2. **Need to understand it?**
   → Read [WEBHOOK_CREATOR_DATA_FLOW.md](WEBHOOK_CREATOR_DATA_FLOW.md) & [WEBHOOK_DATABASE_SCHEMA.md](WEBHOOK_DATABASE_SCHEMA.md)

3. **Need to customize it?**
   → Read [WEBHOOK_CREATOR_RANKING_GUIDE.md](WEBHOOK_CREATOR_RANKING_GUIDE.md)

4. **Need to deploy it?**
   → Read [WEBHOOK_DEPLOYMENT_CHECKLIST.md](WEBHOOK_DEPLOYMENT_CHECKLIST.md)

---

## 📞 Need Help?

**Check the appropriate documentation** based on your question:

| Question | Document |
|----------|----------|
| How do I start Flask? | WEBHOOK_START_HERE.md |
| How do creators flow through the system? | WEBHOOK_CREATOR_DATA_FLOW.md |
| What are the database tables? | WEBHOOK_DATABASE_SCHEMA.md |
| How is risk calculated? | WEBHOOK_CREATOR_RANKING_GUIDE.md |
| How do I set up authentication? | WEBHOOK_INTEGRATION_GUIDE.md |
| How do I deploy to production? | WEBHOOK_DEPLOYMENT_CHECKLIST.md |
| What's broken? | WEBHOOK_ARCHITECTURE_M5.md (Troubleshooting) |

---

**Generated**: 2026-03-03
**Author**: Claude Code
**Status**: Production Ready ✅
