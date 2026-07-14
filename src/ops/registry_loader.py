"""
Operations OS — Registry Loader and Operation Definition Validator.

Responsibilities:
  - Scan src/ops/registry/*.yaml for operation definitions.
  - Parse and structurally validate each definition.
  - Reject duplicate operation_id values.
  - Validate all capability declarations against known contracts and providers.
  - Return immutable (frozen) operation definition objects.

Rules:
  - No Flask imports.
  - No database connections.
  - No HTTP calls.
  - No imports from WATCHTOWER modules.
  - No side effects on import.
  - Invalid definitions always raise — never silently skip.
"""

import re
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from src.ops.contracts import (
    CAPABILITY_CONTRACTS,
    KNOWN_CAPABILITIES,
    MINIMUM_CONTRACT_REQUIRED_STATES,
    SUPPORTED_FRAMEWORK_SCHEMA_VERSIONS,
    VALID_CAPABILITY_STATES,
    VALID_INFRASTRUCTURE_MODELS,
    VALID_LIFECYCLE_STATES,
)
from src.ops.providers import PROVIDER_REGISTRY

# ── Registry location ─────────────────────────────────────────────────────────

_REGISTRY_DIR = Path(__file__).parent / "registry"

# ── Exceptions ────────────────────────────────────────────────────────────────

class RegistryLoadError(Exception):
    """Raised when the registry directory cannot be read or yields no files."""


class OperationDefinitionError(Exception):
    """Raised when a single operation definition is structurally invalid.

    The message always includes the operation file path and the specific
    field or rule that failed.
    """


class UnsupportedFrameworkSchemaError(OperationDefinitionError):
    """Raised when framework_schema_version is not supported by this loader."""


class UnknownCapabilityContractError(OperationDefinitionError):
    """Raised when a SUPPORTED capability references an unknown contract."""


class UnknownCapabilityProviderError(OperationDefinitionError):
    """Raised when a SUPPORTED capability references an unknown provider."""


# ── Operation definition dataclass (immutable view) ───────────────────────────

class OperationDefinition:
    """Immutable container for a validated operation definition.

    Constructed only by the loader — never built directly by callers.
    All mutating operations return copies.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict) -> None:
        # Deep-copy on construction so external mutation cannot affect us.
        object.__setattr__(self, "_data", deepcopy(data))

    def __setattr__(self, name: str, value: Any) -> None:  # type: ignore[override]
        raise AttributeError("OperationDefinition is immutable.")

    # ── Convenience accessors ────────────────────────────────────────────────

    @property
    def operation_id(self) -> str:
        return self._data["operation_id"]

    @property
    def display_name(self) -> str:
        return self._data["display_name"]

    @property
    def status(self) -> str:
        return self._data["status"]

    @property
    def framework_schema_version(self) -> int:
        return self._data["framework_schema_version"]

    @property
    def definition_version(self) -> int:
        return self._data["definition_version"]

    @property
    def operation_version(self) -> Any:
        return self._data.get("operation_version")

    @property
    def infrastructure_model(self) -> str:
        return self._data["infrastructure_model"]

    @property
    def capabilities(self) -> dict:
        return deepcopy(self._data.get("capabilities", {}))

    @property
    def vocabulary(self) -> dict:
        return deepcopy(self._data.get("vocabulary", {}))

    @property
    def assurance_mapping(self) -> dict:
        return deepcopy(self._data.get("assurance_mapping", {}))

    def supported_capabilities(self) -> list[str]:
        """Return names of capabilities with state=SUPPORTED."""
        return [
            name
            for name, decl in self._data.get("capabilities", {}).items()
            if isinstance(decl, dict) and decl.get("state") == "SUPPORTED"
        ]

    def capability_state(self, capability_name: str) -> str:
        """Return the state string for a capability, or NOT_DECLARED if absent."""
        cap = self._data.get("capabilities", {}).get(capability_name)
        if cap is None:
            return "NOT_DECLARED"
        if isinstance(cap, dict):
            return cap.get("state", "NOT_DECLARED")
        return "NOT_DECLARED"

    def raw(self) -> dict:
        """Return a deep copy of the underlying data dict."""
        return deepcopy(self._data)

    def __repr__(self) -> str:
        caps = self.supported_capabilities()
        return (
            f"<OperationDefinition id={self.operation_id!r} "
            f"status={self.status!r} "
            f"supported_capabilities={caps}>"
        )


# ── Validation ────────────────────────────────────────────────────────────────

_OPERATION_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Vocabulary fields required for GRAPH/MESH models (operations with multiple tiers).
_GRAPH_REQUIRED_VOCAB = frozenset({
    "origin_node",
    "intermediate_node",
    "terminal_node",
    "discovery_event",
    "observable_event",
})

# Vocabulary fields required for any model that has a terminal node.
_TERMINAL_REQUIRED_VOCAB = frozenset({"terminal_node"})


def validate_operation_definition(data: dict, source_path: str = "<unknown>") -> None:
    """Validate a parsed operation definition dict.

    Raises a specific subclass of OperationDefinitionError on the first
    structural violation found. Does not return a value.

    Args:
        data:        Parsed YAML dict.
        source_path: File path for error messages.
    """
    ctx = f"[{source_path}]"

    # ── Framework schema version ─────────────────────────────────────────────
    fsv = data.get("framework_schema_version")
    if fsv is None:
        raise OperationDefinitionError(f"{ctx} Missing 'framework_schema_version'.")
    if not isinstance(fsv, int):
        raise OperationDefinitionError(
            f"{ctx} 'framework_schema_version' must be an integer, got {type(fsv).__name__}."
        )
    if fsv not in SUPPORTED_FRAMEWORK_SCHEMA_VERSIONS:
        raise UnsupportedFrameworkSchemaError(
            f"{ctx} framework_schema_version={fsv} is not supported. "
            f"Supported: {sorted(SUPPORTED_FRAMEWORK_SCHEMA_VERSIONS)}."
        )

    # ── Identity ─────────────────────────────────────────────────────────────
    op_id = data.get("operation_id")
    if not op_id:
        raise OperationDefinitionError(f"{ctx} Missing 'operation_id'.")
    if not isinstance(op_id, str) or not _OPERATION_ID_RE.match(op_id):
        raise OperationDefinitionError(
            f"{ctx} 'operation_id' must be lowercase kebab-case, got {op_id!r}."
        )

    if not data.get("display_name"):
        raise OperationDefinitionError(f"{ctx} Missing 'display_name'.")

    # ── Versions ─────────────────────────────────────────────────────────────
    if data.get("definition_version") is None:
        raise OperationDefinitionError(f"{ctx} Missing 'definition_version'.")
    # operation_version may be null/None — explicit absence (key missing) is also acceptable.

    # ── Lifecycle status ─────────────────────────────────────────────────────
    status = data.get("status")
    if not status:
        raise OperationDefinitionError(f"{ctx} Missing 'status'.")
    if status not in VALID_LIFECYCLE_STATES:
        raise OperationDefinitionError(
            f"{ctx} 'status' {status!r} is not valid. "
            f"Allowed: {sorted(VALID_LIFECYCLE_STATES)}."
        )

    # ── Infrastructure model ──────────────────────────────────────────────────
    model = data.get("infrastructure_model")
    if not model:
        raise OperationDefinitionError(f"{ctx} Missing 'infrastructure_model'.")
    if model not in VALID_INFRASTRUCTURE_MODELS:
        raise OperationDefinitionError(
            f"{ctx} 'infrastructure_model' {model!r} is not valid. "
            f"Allowed: {sorted(VALID_INFRASTRUCTURE_MODELS)}."
        )

    graph_cfg = data.get("graph", {}) or {}
    if model == "GRAPH":
        depth = graph_cfg.get("depth")
        if depth is None:
            raise OperationDefinitionError(
                f"{ctx} GRAPH model requires 'graph.depth' to be specified."
            )
        if not isinstance(depth, int) or depth < 1:
            raise OperationDefinitionError(
                f"{ctx} 'graph.depth' must be a positive integer, got {depth!r}."
            )

    # ── Vocabulary ────────────────────────────────────────────────────────────
    vocab = data.get("vocabulary", {}) or {}

    # GRAPH and MESH require the full tier vocabulary including terminal_node.
    # FLAT and NONE do not have a terminal node concept — the requirement is
    # model-conditional. (Defect found in A5: original validator required
    # terminal_node universally, but FLAT/NONE operations have no entity tiers.)
    if model in ("GRAPH", "MESH"):
        for field in _GRAPH_REQUIRED_VOCAB:
            if not vocab.get(field):
                raise OperationDefinitionError(
                    f"{ctx} vocabulary.{field} is required for {model} infrastructure model."
                )

    # FLAT must not define intermediate_node (no intermediate tier exists).
    if model == "FLAT" and vocab.get("intermediate_node"):
        raise OperationDefinitionError(
            f"{ctx} FLAT model must not define vocabulary.intermediate_node "
            f"(no intermediate tier in a flat operation)."
        )

    # NONE must not define any tier vocabulary — it has no infrastructure model.
    if model == "NONE":
        for field in _GRAPH_REQUIRED_VOCAB | {"intermediate_node"}:
            if vocab.get(field):
                raise OperationDefinitionError(
                    f"{ctx} NONE model must not define vocabulary.{field} "
                    f"(no infrastructure tiers in a NONE operation)."
                )

    # ── Capabilities ──────────────────────────────────────────────────────────
    caps = data.get("capabilities", {}) or {}

    health_decl = caps.get("health", {}) or {}
    health_state = health_decl.get("state", "NOT_DECLARED") if isinstance(health_decl, dict) else "NOT_DECLARED"

    for cap_name, decl in caps.items():
        if decl is None:
            decl = {}

        if not isinstance(decl, dict):
            raise OperationDefinitionError(
                f"{ctx} capabilities.{cap_name}: declaration must be a mapping, "
                f"got {type(decl).__name__}."
            )

        state = decl.get("state")
        if not state:
            raise OperationDefinitionError(
                f"{ctx} capabilities.{cap_name}: missing 'state' field."
            )
        if state not in VALID_CAPABILITY_STATES:
            raise OperationDefinitionError(
                f"{ctx} capabilities.{cap_name}.state {state!r} is not valid. "
                f"Allowed: {sorted(VALID_CAPABILITY_STATES)}."
            )

        if state == "SUPPORTED":
            contract = decl.get("contract")
            if not contract:
                raise OperationDefinitionError(
                    f"{ctx} capabilities.{cap_name}: SUPPORTED capability requires 'contract'."
                )
            if contract not in CAPABILITY_CONTRACTS:
                raise UnknownCapabilityContractError(
                    f"{ctx} capabilities.{cap_name}: contract {contract!r} is not "
                    f"registered. Known contracts: {sorted(CAPABILITY_CONTRACTS)}."
                )

            provider = decl.get("provider")
            if not provider:
                raise OperationDefinitionError(
                    f"{ctx} capabilities.{cap_name}: SUPPORTED capability requires 'provider'."
                )
            if provider not in PROVIDER_REGISTRY:
                raise UnknownCapabilityProviderError(
                    f"{ctx} capabilities.{cap_name}: provider {provider!r} is not "
                    f"registered. Known providers: {sorted(PROVIDER_REGISTRY)}."
                )

        elif state == "NOT_DECLARED":
            if decl.get("contract"):
                raise OperationDefinitionError(
                    f"{ctx} capabilities.{cap_name}: NOT_DECLARED capability must not "
                    f"carry 'contract' (found {decl['contract']!r})."
                )
            if decl.get("provider"):
                raise OperationDefinitionError(
                    f"{ctx} capabilities.{cap_name}: NOT_DECLARED capability must not "
                    f"carry 'provider' (found {decl['provider']!r})."
                )

    # ── Minimum contract check for INSTRUMENTED+ ─────────────────────────────
    if status in MINIMUM_CONTRACT_REQUIRED_STATES:
        if health_state != "SUPPORTED":
            raise OperationDefinitionError(
                f"{ctx} Operations with status={status!r} must declare "
                f"capabilities.health with state=SUPPORTED."
            )

    # ── Assurance mapping ────────────────────────────────────────────────────
    amap = data.get("assurance_mapping", {}) or {}
    gti = amap.get("ground_truth_independent")
    if gti is None:
        raise OperationDefinitionError(
            f"{ctx} assurance_mapping.ground_truth_independent must be explicitly "
            f"true or false (missing)."
        )
    if not isinstance(gti, bool):
        raise OperationDefinitionError(
            f"{ctx} assurance_mapping.ground_truth_independent must be a boolean, "
            f"got {type(gti).__name__}."
        )


# ── Loader ─────────────────────────────────────────────────────────────────────

_registry: dict[str, OperationDefinition] | None = None


def load_registry(registry_dir: Path | None = None) -> dict[str, OperationDefinition]:
    """Scan the registry directory and return all valid operation definitions.

    Args:
        registry_dir: Override the default registry location (used in tests).

    Returns:
        Dict mapping operation_id → OperationDefinition.

    Raises:
        RegistryLoadError:          Directory unreadable or no YAML files found.
        OperationDefinitionError:   Any file fails validation.
        RegistryLoadError:          Duplicate operation_id across files.
    """
    target = registry_dir or _REGISTRY_DIR

    if not target.is_dir():
        raise RegistryLoadError(
            f"Registry directory does not exist: {target}"
        )

    yaml_files = sorted(target.glob("*.yaml"))
    if not yaml_files:
        raise RegistryLoadError(
            f"No .yaml files found in registry directory: {target}"
        )

    operations: dict[str, OperationDefinition] = {}

    for yaml_path in yaml_files:
        try:
            raw = yaml_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RegistryLoadError(
                f"Cannot read registry file {yaml_path}: {exc}"
            ) from exc

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise OperationDefinitionError(
                f"[{yaml_path}] YAML parse error: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise OperationDefinitionError(
                f"[{yaml_path}] Expected a YAML mapping at the top level, "
                f"got {type(data).__name__}."
            )

        validate_operation_definition(data, source_path=str(yaml_path))

        op_id = data["operation_id"]
        if op_id in operations:
            raise RegistryLoadError(
                f"Duplicate operation_id {op_id!r}: found in both "
                f"{yaml_path.name} and a previously loaded file."
            )

        operations[op_id] = OperationDefinition(data)

    return operations


def get_operation(operation_id: str, registry_dir: Path | None = None) -> OperationDefinition:
    """Return one operation definition by ID.

    Loads the full registry on first call (cached per process for the
    default registry location). Raises KeyError if not found.
    """
    global _registry
    if _registry is None and registry_dir is None:
        _registry = load_registry()
    registry = _registry if registry_dir is None else load_registry(registry_dir)

    if operation_id not in registry:
        raise KeyError(
            f"Operation {operation_id!r} not found. "
            f"Registered: {sorted(registry)}."
        )
    return registry[operation_id]


def list_operations(registry_dir: Path | None = None) -> list[str]:
    """Return sorted list of all registered operation IDs."""
    global _registry
    if _registry is None and registry_dir is None:
        _registry = load_registry()
    registry = _registry if registry_dir is None else load_registry(registry_dir)
    return sorted(registry)
