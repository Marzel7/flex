"""
Operations OS — Capability Resolver.

Resolves a capability from a registered operation into a validated payload.

Steps:
  1. Read the capability declaration from the OperationDefinition.
  2. Look up the provider descriptor from the provider registry.
  3. Fetch the payload via the provider (HTTP for now).
  4. Validate the payload against the declared contract.
  5. Return a CapabilityResult containing the payload and any validation errors.

Rules:
  - No Flask imports.
  - No database connections.
  - No WATCHTOWER-specific imports.
  - The resolver does not know what operation it is serving.
  - All errors are captured in CapabilityResult, never raised to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ops.contracts import validate_capability_payload, CAPABILITY_CONTRACTS
from src.ops.providers import get_provider, ProviderDescriptor
from src.ops.registry_loader import OperationDefinition


@dataclass
class CapabilityResult:
    """The outcome of resolving one capability."""
    capability_name:  str
    state:            str           # SUPPORTED | UNSUPPORTED | NOT_DECLARED
    contract:         str | None    # contract name if SUPPORTED
    provider_id:      str | None    # logical provider name if SUPPORTED
    provider_path:    str | None    # resolved HTTP path (informational)
    payload:          dict | None   # raw response from provider, or None
    contract_errors:  list[str]     # non-empty = INVALID_CONTRACT
    fetch_error:      str | None    # non-empty = fetch failed

    @property
    def valid(self) -> bool:
        return not self.contract_errors and not self.fetch_error

    @property
    def render_status(self) -> str:
        """
        VALID            — payload present, contract satisfied
        INVALID_CONTRACT — payload present, contract violated
        FETCH_ERROR      — provider call failed
        UNSUPPORTED      — state=UNSUPPORTED
        NOT_DECLARED     — state=NOT_DECLARED
        """
        if self.state == "NOT_DECLARED":
            return "NOT_DECLARED"
        if self.state == "UNSUPPORTED":
            return "UNSUPPORTED"
        if self.fetch_error:
            return "FETCH_ERROR"
        if self.contract_errors:
            return "INVALID_CONTRACT"
        return "VALID"


def resolve_capability(
    op: OperationDefinition,
    capability_name: str,
    http_fetcher=None,
) -> CapabilityResult:
    """Resolve one capability for an operation.

    Args:
        op:               The validated operation definition.
        capability_name:  Which capability to resolve.
        http_fetcher:     Callable(path: str) -> dict. Injected so the resolver
                          has no import-time HTTP dependency and is fully testable.
                          Signature: fetcher(path) raises on network error,
                          returns a dict on success.

    Returns:
        CapabilityResult with populated fields. Never raises.
    """
    caps = op.capabilities
    decl = caps.get(capability_name)

    # Capability not mentioned in definition at all → treat as NOT_DECLARED
    if decl is None:
        return CapabilityResult(
            capability_name=capability_name,
            state="NOT_DECLARED",
            contract=None,
            provider_id=None,
            provider_path=None,
            payload=None,
            contract_errors=[],
            fetch_error=None,
        )

    state = decl.get("state", "NOT_DECLARED")

    if state in ("NOT_DECLARED", "UNSUPPORTED"):
        return CapabilityResult(
            capability_name=capability_name,
            state=state,
            contract=None,
            provider_id=None,
            provider_path=None,
            payload=None,
            contract_errors=[],
            fetch_error=None,
        )

    # SUPPORTED — resolve provider and fetch
    contract_name = decl.get("contract")
    provider_id   = decl.get("provider")

    # Resolve provider descriptor
    try:
        descriptor: ProviderDescriptor = get_provider(provider_id)
        provider_path = descriptor.path
    except KeyError as exc:
        return CapabilityResult(
            capability_name=capability_name,
            state=state,
            contract=contract_name,
            provider_id=provider_id,
            provider_path=None,
            payload=None,
            contract_errors=[],
            fetch_error=str(exc),
        )

    # Fetch payload
    payload: dict | None = None
    fetch_error: str | None = None

    if http_fetcher is None:
        fetch_error = "No HTTP fetcher provided (framework shell must inject one)."
    else:
        try:
            payload = http_fetcher(provider_path)
        except Exception as exc:  # noqa: BLE001
            fetch_error = f"Provider fetch failed for {provider_path!r}: {exc}"

    # Validate payload against contract
    contract_errors: list[str] = []
    if payload is not None and contract_name:
        contract_errors = validate_capability_payload(contract_name, payload)

    return CapabilityResult(
        capability_name=capability_name,
        state=state,
        contract=contract_name,
        provider_id=provider_id,
        provider_path=provider_path,
        payload=payload,
        contract_errors=contract_errors,
        fetch_error=fetch_error,
    )


def resolve_all_capabilities(
    op: OperationDefinition,
    http_fetcher=None,
    only: list[str] | None = None,
) -> dict[str, CapabilityResult]:
    """Resolve all (or a subset of) capabilities for an operation.

    Args:
        op:           Validated operation definition.
        http_fetcher: Injected HTTP fetcher (see resolve_capability).
        only:         If provided, only resolve these capability names.

    Returns:
        Dict mapping capability_name → CapabilityResult.
        Includes NOT_DECLARED results for every known capability so the
        shell always has a complete picture.
    """
    from src.ops.contracts import KNOWN_CAPABILITIES

    target_caps = set(only) if only else KNOWN_CAPABILITIES

    results: dict[str, CapabilityResult] = {}
    for cap_name in sorted(target_caps):
        results[cap_name] = resolve_capability(op, cap_name, http_fetcher)
    return results
