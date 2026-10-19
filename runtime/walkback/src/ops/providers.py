"""
Operations OS — Provider Registry.

A provider is a logical name that resolves to a descriptor describing how
the framework should retrieve data for a capability. The registry YAML files
name providers by logical ID (e.g. "watchtower_health"), never by raw URLs.

This module maps logical provider IDs to descriptors. The framework shell
(built in a future sprint) will use these descriptors to fetch data.

Provider types:
  HTTP      — data is fetched from an HTTP endpoint within the application
  CALLABLE  — data is returned by a Python callable (future use)
  SNAPSHOT  — data is read from a static snapshot file (future use)
  CUSTOM    — operation-defined retrieval (future use)

Rules:
  - No Flask, no database, no network imports in this module.
  - This module is imported at startup; it must be side-effect free.
  - URLs here are internal application paths, not external URLs.
  - Only HTTP is implemented now. CALLABLE/SNAPSHOT/CUSTOM are declared for
    forward-compatibility but will raise NotImplementedError if invoked.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderDescriptor:
    """Immutable descriptor for a capability data provider."""
    provider_id:      str
    provider_type:    str          # HTTP | CALLABLE | SNAPSHOT | CUSTOM
    # HTTP-specific fields
    path:             str  = ""    # internal application path, e.g. /api/ops-v2/detection-health
    response_adapter: str  = ""    # logical adapter name (used by framework shell, not evaluated here)
    # Forward-compat: extra metadata an operation may supply
    meta:             dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_types = {"HTTP", "CALLABLE", "SNAPSHOT", "CUSTOM"}
        if self.provider_type not in valid_types:
            raise ValueError(
                f"Provider {self.provider_id!r}: invalid type {self.provider_type!r}. "
                f"Must be one of {sorted(valid_types)}."
            )
        if self.provider_type == "HTTP" and not self.path:
            raise ValueError(
                f"Provider {self.provider_id!r}: HTTP provider requires a non-empty path."
            )


# ── Provider registry ─────────────────────────────────────────────────────────
# Keys are logical provider IDs referenced in operation definitions.
# Values are ProviderDescriptors.
#
# WATCHTOWER providers point to the existing WATCHTOWER API endpoints.
# These endpoints are not changed by this sprint — the provider declaration
# is the translation layer between the generic framework and existing routes.

PROVIDER_REGISTRY: dict[str, ProviderDescriptor] = {

    "watchtower_health": ProviderDescriptor(
        provider_id="watchtower_health",
        provider_type="HTTP",
        path="/api/ops-v2/detection-health",
        response_adapter="watchtower_health_adapter",
    ),

    "watchtower_discovery": ProviderDescriptor(
        provider_id="watchtower_discovery",
        provider_type="HTTP",
        path="/api/ops-v2/discovery-assurance",
        response_adapter="watchtower_discovery_adapter",
    ),

    "watchtower_alerts": ProviderDescriptor(
        provider_id="watchtower_alerts",
        provider_type="HTTP",
        path="/api/ops-v2/alerts",
        response_adapter="watchtower_alerts_adapter",
    ),

    # ── Launcher Observatory providers ───────────────────────────────────────
    "launcher_observatory_health": ProviderDescriptor(
        provider_id="launcher_observatory_health",
        provider_type="HTTP",
        path="/api/ops/launcher-observatory/health",
        response_adapter="passthrough",
    ),

    "launcher_observatory_failure_attribution": ProviderDescriptor(
        provider_id="launcher_observatory_failure_attribution",
        provider_type="HTTP",
        path="/api/ops/launcher-observatory/failure-attribution",
        response_adapter="passthrough",
    ),

    "launcher_observatory_behaviour": ProviderDescriptor(
        provider_id="launcher_observatory_behaviour",
        provider_type="HTTP",
        path="/api/ops/launcher-observatory/behaviour",
        response_adapter="passthrough",
    ),

    "launcher_observatory_intelligence": ProviderDescriptor(
        provider_id="launcher_observatory_intelligence",
        provider_type="HTTP",
        path="/api/ops/launcher-observatory/intelligence",
        response_adapter="passthrough",
    ),

    "launcher_observatory_outcome_intelligence": ProviderDescriptor(
        provider_id="launcher_observatory_outcome_intelligence",
        provider_type="HTTP",
        path="/api/ops/launcher-observatory/outcome-intelligence",
        response_adapter="passthrough",
    ),

    # ── Buy Swarm Observatory providers (O3) ─────────────────────────────────
    "buy_swarm_observatory_health": ProviderDescriptor(
        provider_id="buy_swarm_observatory_health",
        provider_type="HTTP",
        path="/api/ops/buy-swarm-observatory/health",
        response_adapter="passthrough",
    ),
    "buy_swarm_observatory_failure_attribution": ProviderDescriptor(
        provider_id="buy_swarm_observatory_failure_attribution",
        provider_type="HTTP",
        path="/api/ops/buy-swarm-observatory/failure-attribution",
        response_adapter="passthrough",
    ),
    "buy_swarm_observatory_behaviour": ProviderDescriptor(
        provider_id="buy_swarm_observatory_behaviour",
        provider_type="HTTP",
        path="/api/ops/buy-swarm-observatory/behaviour",
        response_adapter="passthrough",
    ),
    "buy_swarm_observatory_intelligence": ProviderDescriptor(
        provider_id="buy_swarm_observatory_intelligence",
        provider_type="HTTP",
        path="/api/ops/buy-swarm-observatory/intelligence",
        response_adapter="passthrough",
    ),
    "buy_swarm_observatory_outcome_intelligence": ProviderDescriptor(
        provider_id="buy_swarm_observatory_outcome_intelligence",
        provider_type="HTTP",
        path="/api/ops/buy-swarm-observatory/outcome-intelligence",
        response_adapter="passthrough",
    ),

    # ── Observatory Demo providers (A5 validation operation) ─────────────────
    "demo_health": ProviderDescriptor(
        provider_id="demo_health",
        provider_type="HTTP",
        path="/api/ops-demo/health",
        response_adapter="passthrough",
    ),

    "demo_failure_attribution": ProviderDescriptor(
        provider_id="demo_failure_attribution",
        provider_type="HTTP",
        path="/api/ops-demo/failure-attribution",
        response_adapter="passthrough",
    ),

    "demo_alerts": ProviderDescriptor(
        provider_id="demo_alerts",
        provider_type="HTTP",
        path="/api/ops-demo/alerts",
        response_adapter="passthrough",
    ),
}


def get_provider(provider_id: str) -> ProviderDescriptor:
    """Return the descriptor for a logical provider ID.

    Raises KeyError if the provider is not registered.
    """
    if provider_id not in PROVIDER_REGISTRY:
        raise KeyError(
            f"Unknown provider: {provider_id!r}. "
            f"Registered providers: {sorted(PROVIDER_REGISTRY)}"
        )
    return PROVIDER_REGISTRY[provider_id]


def list_providers() -> list[str]:
    """Return sorted list of all registered provider IDs."""
    return sorted(PROVIDER_REGISTRY)
