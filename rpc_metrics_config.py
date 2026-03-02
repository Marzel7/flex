"""
RPC Metrics Configuration

Customize credit schedules, plan budgets, and alerting thresholds here.

OFFICIAL HELIUS DOCUMENTATION:
- Credits & Pricing: https://www.helius.dev/docs/billing/credits
- Rate Limits: https://www.helius.dev/docs/billing/rate-limits
- API Methods: https://docs.helius.xyz/

All credit rates are from official Helius documentation.
Enhanced Transactions are 100 credits per request (official rate).
"""

# ============================================================================
# HELIUS CREDIT SCHEDULE
# ============================================================================
# Based on official Helius pricing documentation
# https://www.helius.dev/docs/billing/credits
#
# IMPORTANT: Some methods (like Enhanced Transactions) have plan-dependent costs.
# See documentation in CREDIT_SCHEDULE below for which ones need manual verification.

CREDIT_SCHEDULE = {
    # Standard RPC methods (low cost)
    "getHealth": 1,
    "getClusterNodes": 1,
    "getSystemProgram": 1,
    "getVersion": 1,
    "getEpochInfo": 1,
    "getGenesisHash": 1,
    "getIdentity": 1,
    "getInflationGovernor": 1,
    "getInflationRate": 1,
    "getInflationReward": 1,
    "getLargestAccounts": 1,
    "getLeaderSchedule": 1,
    "getMaxRetransmitSlot": 1,
    "getMaxShredInsertSlot": 1,
    "getMultipleAccounts": 1,
    "getProgramAccounts": 5,  # Slightly higher for historical data
    "getSignatureStatuses": {
        "default": 1,
        "searchTransactionHistory": 10,
    },
    "getSlot": 1,
    "getSlotLeader": 1,
    "getSlotLeaders": 1,
    "getSupply": 1,
    "getTokenAccountsByDelegate": 1,
    "getTokenAccountsByOwner": 1,
    "getTokenLargestAccounts": 1,
    "getTokenSupply": 1,
    "getTransaction": 10,  # Historical transaction (archival)
    "getTransactionCount": 1,
    "getAccountInfo": 1,                        # Account lookup (newly added)
    "getBalance": 1,                            # SOL balance check (newly added)
    "getTokenAccountBalance": 1,                # Token balance check (newly added)
    "getBlock": 1,                              # Block data (newly added)
    "getSignaturesForAddress": 10,              # Transaction history for address

    # Helius-exclusive RPC methods (high cost)
    "getTransactionsForAddress": 100,

    # Streaming (handled separately via bytes)
    # "laserstream_bytes": 3 per 0.1MB
    # "enhanced_ws_bytes": 3 per 0.1MB

    # Helius Enhanced Transactions API (REST pseudo-methods)
    # Source: https://www.helius.dev/docs/billing/credits
    # "Enhanced transaction parsing" = 100 credits per request
    # This applies to:
    # - GET /v0/addresses/{address}/transactions (Enhanced Transactions)
    # - POST /v0/transactions (Batch Enhanced Transactions)
    # - All Enhanced Transactions REST endpoints
    "helius_enhanced_addresses_transactions": 100,  # Per request (official docs)
    "helius_enhanced_transactions_batch": 100,       # Per request (official docs)

    # Fallback RPC methods
    "fallback_getTransaction": 10,
    "fallback_getSignaturesForAddress": 10,
}

# Streaming credits: 3 credits per 0.1MB
STREAMING_CREDITS_PER_BYTE = 3.0 / (0.1 * 1024 * 1024)

# ============================================================================
# PLAN CONFIGURATION
# ============================================================================

class PlanConfig:
    """Helius plan configuration"""

    # Available plans and their monthly credit budgets
    PLANS = {
        "free": {"monthly_credits": 100_000, "rate_limit_per_sec": 1},
        "developer": {"monthly_credits": 2_000_000, "rate_limit_per_sec": 10},
        "business": {"monthly_credits": 50_000_000, "rate_limit_per_sec": 100},
        "professional": {"monthly_credits": 500_000_000, "rate_limit_per_sec": 1000},
        "unlimited": {"monthly_credits": 999_999_999, "rate_limit_per_sec": 10000},
    }

    # Your current plan (set in environment or here)
    CURRENT_PLAN = "business"  # Change to your plan

    # ACTUAL USAGE FROM HELIUS DASHBOARD (as of 2026-03-02 after reset)
    # This is synced from your Helius account
    CURRENT_USAGE = {
        "credits_used_today": 17_575,        # Total credits consumed today
        "credits_remaining": 982_425,        # Remaining monthly budget (1M - 17,575)
        "budget_start_date": "2026-03-01",   # Start of billing period
    }

    @classmethod
    def get_monthly_credits(cls):
        """Get monthly credit budget for current plan"""
        return cls.PLANS[cls.CURRENT_PLAN]["monthly_credits"]

    @classmethod
    def get_rate_limit_per_sec(cls):
        """Get rate limit (requests per second) for current plan"""
        return cls.PLANS[cls.CURRENT_PLAN]["rate_limit_per_sec"]


# ============================================================================
# ALERTING THRESHOLDS
# ============================================================================

class AlertConfig:
    """Configure alerting thresholds"""

    # Burn rate alert: trigger if credits/min exceeds this
    BURN_RATE_THRESHOLD_PER_MINUTE = 100.0

    # Budget alerts: trigger at these remaining percentages
    BUDGET_WARNING_PERCENT = 20  # Warn when < 20% remaining
    BUDGET_CRITICAL_PERCENT = 5  # Critical when < 5% remaining

    # Error rate: trigger if error % exceeds this
    ERROR_RATE_THRESHOLD_PERCENT = 5.0

    # Rate limit: trigger if 429 count exceeds this per interval
    RATE_LIMIT_THRESHOLD_PER_5MIN = 10

    # High latency: p95 latency per section exceeds this
    HIGH_LATENCY_THRESHOLD_MS = 5000


# ============================================================================
# SECTION CONFIGURATION
# ============================================================================

class SectionConfig:
    """Define recognized sections and their expected credit budgets"""

    SECTIONS = {
        "listener": {
            "description": "WebSocket listener for token creation",
            "examples": ["Pump.Fun stream", "LaserStream"],
            "expected_credits_per_day": 50_000,  # For budgeting
            "methods": ["laserstream_bytes", "enhanced_ws_bytes"],
        },
        "creator_funding": {
            "description": "Extract creator funding relationships",
            "examples": ["Real-time token creator detection"],
            "expected_credits_per_day": 30_000,
            "methods": ["getTransaction", "helius_enhanced_addresses_transactions"],
        },
        "funder_incoming": {
            "description": "Extract funder's incoming transfers",
            "examples": ["Trace where funders got money"],
            "expected_credits_per_day": 100_000,  # Highest volume
            "methods": ["helius_enhanced_addresses_transactions", "getTransactionsForAddress"],
        },
        "creator_outgoing_scan": {
            "description": "Background scan of creator outgoing transfers",
            "examples": ["12-hour enrichment job"],
            "expected_credits_per_day": 20_000,
            "methods": ["helius_enhanced_addresses_transactions"],
        },
        "ui_api": {
            "description": "Flask API endpoints for dashboard",
            "examples": ["User-triggered analysis"],
            "expected_credits_per_day": 15_000,
            "methods": ["getTransaction", "getSignaturesForAddress"],
        },
        "background_enrichment": {
            "description": "Background enrichment jobs",
            "examples": ["Batch processing", "Historical analysis"],
            "expected_credits_per_day": 10_000,
            "methods": ["helius_enhanced_transactions_batch", "getTransactionsForAddress"],
        },
    }

    @classmethod
    def get_section_budget(cls, section: str) -> int:
        """Get expected daily credit budget for a section"""
        return cls.SECTIONS.get(section, {}).get("expected_credits_per_day", 0)


# ============================================================================
# PROVIDER CONFIGURATION
# ============================================================================

class ProviderConfig:
    """Configure RPC providers"""

    PROVIDERS = {
        "helius_rpc": {
            "name": "Helius RPC",
            "url": "https://mainnet.helius-rpc.com",
            "type": "rpc",
        },
        "helius_enhanced": {
            "name": "Helius Enhanced Transactions",
            "url": "https://api.helius.xyz",
            "type": "rest",
        },
        "public_rpc_fallback": {
            "name": "Public RPC Fallback",
            "url": "https://api.mainnet-beta.solana.com",
            "type": "rpc",
        },
        "alchemy_fallback": {
            "name": "Alchemy RPC",
            "url": "https://solana-mainnet.g.alchemy.com/v2/",
            "type": "rpc",
        },
        "quicknode_fallback": {
            "name": "QuickNode RPC",
            "url": "https://solana-mainnet.quiknode.pro/",
            "type": "rpc",
        },
    }

    @classmethod
    def get_provider_url(cls, provider: str) -> str:
        """Get URL for a provider"""
        return cls.PROVIDERS.get(provider, {}).get("url", "")


# ============================================================================
# METRICS API CONFIGURATION
# ============================================================================

class MetricsAPIConfig:
    """Configure metrics API server"""

    # Server settings
    HOST = "0.0.0.0"
    PORT = 8001
    WORKERS = 2
    LOG_LEVEL = "info"

    # Metrics settings
    MAX_HISTORY = 10000  # Keep last N request records
    REFRESH_INTERVAL_MS = 5000  # Dashboard auto-refresh

    # Security
    ADMIN_TOKEN = "CHANGE_ME"  # Update this!

    # Monitoring
    METRICS_EXPORT_PATH = "/tmp/flex_metrics.json"  # For external monitoring


# ============================================================================
# COST GOVERNOR CONFIGURATION
# ============================================================================

class CostGovernorConfig:
    """Configure automatic cost controls"""

    # Enable/disable cost governor
    ENABLED = True

    # Check interval (seconds)
    CHECK_INTERVAL = 60

    # Thresholds for cost control actions
    THRESHOLDS = {
        # If burn rate exceeds this, reduce concurrency
        "high_burn_rate": 500.0,  # credits/min

        # If daily estimate exceeds this, pause background scans
        "daily_estimate_limit": 500_000,  # credits/day

        # If monthly estimate exceeds budget, reduce page depth
        "monthly_estimate_limit": PlanConfig.get_monthly_credits() * 0.8,  # 80% of budget
    }

    # Actions to take when thresholds exceeded
    ACTIONS = {
        "high_burn_rate": {
            "description": "Reduce funder_incoming concurrency and page depth",
            "funder_max_pages": 1,  # Reduce from 5
            "funder_concurrency": 2,  # Reduce from 10
            "creator_scan_pause": False,
        },
        "daily_estimate_limit": {
            "description": "Pause background enrichment scans",
            "pause_background": True,
            "funder_concurrency": 1,
            "funder_max_pages": 1,
        },
        "monthly_estimate_limit": {
            "description": "Critical: halt all optional scanning",
            "pause_background": True,
            "pause_listener": False,  # Keep listener running
            "funder_concurrency": 1,
            "funder_max_pages": 1,
        },
    }


# ============================================================================
# EXPORT CONFIGURATION
# ============================================================================

def get_config() -> dict:
    """Get full configuration as dict"""
    return {
        "credit_schedule": CREDIT_SCHEDULE,
        "plan": {
            "current": PlanConfig.CURRENT_PLAN,
            "monthly_credits": PlanConfig.get_monthly_credits(),
            "rate_limit_per_sec": PlanConfig.get_rate_limit_per_sec(),
        },
        "alerts": {
            "burn_rate_threshold_per_minute": AlertConfig.BURN_RATE_THRESHOLD_PER_MINUTE,
            "budget_warning_percent": AlertConfig.BUDGET_WARNING_PERCENT,
            "budget_critical_percent": AlertConfig.BUDGET_CRITICAL_PERCENT,
            "error_rate_threshold_percent": AlertConfig.ERROR_RATE_THRESHOLD_PERCENT,
        },
        "sections": SectionConfig.SECTIONS,
        "providers": ProviderConfig.PROVIDERS,
        "api": {
            "host": MetricsAPIConfig.HOST,
            "port": MetricsAPIConfig.PORT,
            "max_history": MetricsAPIConfig.MAX_HISTORY,
        },
        "cost_governor": {
            "enabled": CostGovernorConfig.ENABLED,
            "check_interval": CostGovernorConfig.CHECK_INTERVAL,
            "thresholds": CostGovernorConfig.THRESHOLDS,
        },
    }


if __name__ == "__main__":
    # Print configuration
    import json
    print(json.dumps(get_config(), indent=2))
