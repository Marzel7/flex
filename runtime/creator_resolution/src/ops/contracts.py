"""
Operations OS — Capability Contract Registry.

A contract names the required fields, their types, and their display metadata.
The shell uses display metadata to render capability cards without any
capability-specific conditional logic.

Rules:
- All field names in contracts are generic (no WATCHTOWER terminology).
- A contract validates structure only — not semantic correctness.
- Optional fields are listed but not enforced.
- Display metadata is optional per contract; its absence produces a generic fallback.
- No Flask, no database, no network imports permitted in this module.

─────────────────────────────────────────────────────────
Display metadata spec
─────────────────────────────────────────────────────────

display:
  title:          str            Override label shown in card header.
  primary_metric: FieldDisplay   Single headline field — rendered large.
  summary_fields: [FieldDisplay] Ordered list of fields to render as key-value rows.
  status_field:   StatusDisplay  Which field is the status and how to colour it.
  empty_state:    str            Message when payload is missing or empty.
  warning_rules:  [WarningRule]  Conditional highlight rules evaluated at render time.

FieldDisplay:
  field:          str            Dotted path into payload (e.g. "summary.attribution_rate_pct")
  label:          str            Human-readable label.
  format:         FormatType     One of: plain | bool_yesno | timestamp_ago | pct |
                                         count | count_warn_nonzero | breakdown_dict
  unit:           str | None     Suffix appended after value (e.g. "%", "s").
  fallback:       str            Value to show if field is absent.

StatusDisplay:
  field:          str            Field path.
  values:
    <value>: colour             colour ∈ { green | amber | red | dim | cyan | purple }

WarningRule:
  field:          str            Field to test.
  condition:      ConditionType  gt_zero | equals | not_equals
  compare:        any | None     Value to compare against (for equals/not_equals).
  colour:         colour         Colour to apply to that field's value when rule fires.
"""

from typing import Any

_NoneType = type(None)


# ── Display format types ──────────────────────────────────────────────────────
# The shell's JS renderer handles each type. Adding a new type requires only a
# JS handler addition — not a template restructure.
FORMAT_TYPES = frozenset({
    "plain",               # render value as-is
    "bool_yesno",          # True → "yes", False → "no"
    "timestamp_ago",       # unix int → "Xm ago" (relative, computed client-side)
    "pct",                 # float → "45.5%"
    "count",               # int → formatted integer
    "count_warn_nonzero",  # int → formatted integer, red when > 0
    "breakdown_dict",      # dict → rendered as sub-key-value list
})

COLOUR_VALUES = frozenset({
    "green", "amber", "red", "dim", "cyan", "purple", "default",
})


# ── Contract entries ──────────────────────────────────────────────────────────

CAPABILITY_CONTRACTS: dict[str, dict[str, Any]] = {

    # ── health_v1 ─────────────────────────────────────────────────────────────
    "health_v1": {
        "required": {
            "status":                  str,
            "pipeline_active":         bool,
            "last_event_detected_at":  (int, _NoneType),
        },
        "optional": {
            "worker_states":           dict,
            "active_alert_count":      int,
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": ["HEALTHY", "DEGRADED", "OFFLINE", "UNKNOWN"],

        "display": {
            "title": "Health",
            "status_field": {
                "field": "status",
                "values": {
                    "HEALTHY":  "green",
                    "DEGRADED": "amber",
                    "OFFLINE":  "red",
                    "UNKNOWN":  "dim",
                },
            },
            "primary_metric": {
                "field":    "status",
                "label":    "Pipeline Status",
                "format":   "plain",
                "fallback": "UNKNOWN",
            },
            "summary_fields": [
                {
                    "field":    "pipeline_active",
                    "label":    "Pipeline Active",
                    "format":   "bool_yesno",
                    "fallback": "—",
                },
                {
                    "field":    "last_event_detected_at",
                    "label":    "Last Event",
                    "format":   "timestamp_ago",
                    "fallback": "never",
                },
                {
                    "field":    "active_alert_count",
                    "label":    "Active Alerts",
                    "format":   "count_warn_nonzero",
                    "fallback": "0",
                },
            ],
            "empty_state": "No health data available.",
        },
    },

    # ── infrastructure_v1 ────────────────────────────────────────────────────
    "infrastructure_v1": {
        "required": {
            "origin_node_count":       int,
            "terminal_node_count":     int,
        },
        "optional": {
            "intermediate_node_count": int,
            "graph_edge_count":        int,
            "excluded_node_count":     int,
            "last_graph_update_at":    (int, _NoneType),
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Infrastructure",
            "primary_metric": {
                "field":    "origin_node_count",
                "label":    "Origin Nodes",
                "format":   "count",
                "fallback": "0",
            },
            "summary_fields": [
                {
                    "field":    "origin_node_count",
                    "label":    "Origin Nodes",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "intermediate_node_count",
                    "label":    "Intermediate Nodes",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "terminal_node_count",
                    "label":    "Terminal Nodes",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "excluded_node_count",
                    "label":    "Excluded Nodes",
                    "format":   "count",
                    "fallback": "0",
                },
                {
                    "field":    "last_graph_update_at",
                    "label":    "Last Updated",
                    "format":   "timestamp_ago",
                    "fallback": "never",
                },
            ],
            "empty_state": "No infrastructure data available.",
        },
    },

    # ── discovery_assurance_v1 ───────────────────────────────────────────────
    "discovery_assurance_v1": {
        "required": {
            "total_observed_outcomes":  int,
            "attributed_outcomes":      int,
            "unattributed_outcomes":    int,
            "attribution_rate_pct":     (int, float),
        },
        "optional": {
            "topology_complete_rate_pct": (int, float, _NoneType),
            "pre_signal_rate_pct":        (int, float, _NoneType),
            "label":                      (str, _NoneType),
            "scope_note":                 (str, _NoneType),
            "generated_at":               int,
            "ok":                         bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Discovery Assurance",
            "primary_metric": {
                "field":    "attribution_rate_pct",
                "label":    "Attribution Rate",
                "format":   "pct",
                "fallback": "—",
            },
            "summary_fields": [
                {
                    "field":    "total_observed_outcomes",
                    "label":    "Total Outcomes",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "attributed_outcomes",
                    "label":    "Attributed",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "unattributed_outcomes",
                    "label":    "Unattributed",
                    "format":   "count_warn_nonzero",
                    "fallback": "—",
                },
                {
                    "field":    "topology_complete_rate_pct",
                    "label":    "Topology Complete",
                    "format":   "pct",
                    "fallback": "—",
                },
            ],
            "warning_rules": [
                {
                    "field":     "unattributed_outcomes",
                    "condition": "gt_zero",
                    "colour":    "red",
                },
            ],
            "empty_state": "No discovery assurance data available.",
        },
    },

    # ── failure_attribution_v1 ───────────────────────────────────────────────
    "failure_attribution_v1": {
        "required": {
            "failure_breakdown":       dict,
        },
        "optional": {
            "worst_nodes":             dict,
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Failure Attribution",
            "primary_metric": {
                "field":    "failure_breakdown",
                "label":    "Failure Breakdown",
                "format":   "breakdown_dict",
                "fallback": "—",
            },
            "summary_fields": [
                {
                    "field":    "failure_breakdown",
                    "label":    "Failure Breakdown",
                    "format":   "breakdown_dict",
                    "fallback": "—",
                },
            ],
            "empty_state": "No failure attribution data available.",
        },
    },

    # ── behaviour_v1 ─────────────────────────────────────────────────────────
    # Describes observed operational behaviour of a persistent operator.
    # All fields are retrospective — no predictions.
    "behaviour_v1": {
        "required": {
            "operator_count":          int,
            "total_launches":          int,
            "avg_launches_per_operator": (int, float),
        },
        "optional": {
            "active_operators":        int,
            "dormant_operators":       int,
            "retired_operators":       int,
            "new_operators_30d":       int,
            "avg_campaign_interval_days": (int, float, _NoneType),
            "median_campaign_length_days": (int, float, _NoneType),
            "burst_operator_count":    int,
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Behaviour",
            "primary_metric": {
                "field":    "operator_count",
                "label":    "Persistent Operators",
                "format":   "count",
                "fallback": "0",
            },
            "summary_fields": [
                {
                    "field":    "operator_count",
                    "label":    "Total Operators",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "total_launches",
                    "label":    "Total Launches",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "avg_launches_per_operator",
                    "label":    "Avg Launches / Operator",
                    "format":   "plain",
                    "fallback": "—",
                },
                {
                    "field":    "active_operators",
                    "label":    "Active (30d)",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "new_operators_30d",
                    "label":    "New (30d)",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "avg_campaign_interval_days",
                    "label":    "Avg Campaign Interval",
                    "format":   "plain",
                    "fallback": "—",
                },
            ],
            "empty_state": "No behaviour data available.",
        },
    },

    # ── intelligence_v1 ──────────────────────────────────────────────────────
    # Operator-level intelligence: who, ranked by significance.
    # top_operators / most_active_30d may be a dict (label→count) or list of records.
    "intelligence_v1": {
        "required": {
            "top_operators":           (list, dict),
        },
        "optional": {
            "returning_operators":     (list, dict),
            "new_operators":           (list, dict),
            "most_active_30d":         (list, dict),
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Intelligence",
            "primary_metric": {
                "field":    "top_operators",
                "label":    "Top Operators",
                "format":   "breakdown_dict",
                "fallback": "—",
            },
            "summary_fields": [
                {
                    "field":    "top_operators",
                    "label":    "Top Operators by Launch Count",
                    "format":   "breakdown_dict",
                    "fallback": "—",
                },
                {
                    "field":    "most_active_30d",
                    "label":    "Most Active (30d)",
                    "format":   "breakdown_dict",
                    "fallback": "—",
                },
            ],
            "empty_state": "No intelligence data available.",
        },
    },

    # ── outcome_intelligence_v1 ───────────────────────────────────────────────
    # Measures how much Unknown Scope is explained by persistent operators.
    "outcome_intelligence_v1": {
        "required": {
            "unknown_scope_total":     int,
            "explained_by_operators":  int,
            "remaining_unknown":       int,
            "explanation_rate_pct":    (int, float),
        },
        "optional": {
            "watchtower_scope_total":  int,
            "persistent_funders":      int,
            "threshold_description":   str,
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Outcome Intelligence",
            "primary_metric": {
                "field":    "explanation_rate_pct",
                "label":    "Unknown Scope Explained",
                "format":   "pct",
                "fallback": "—",
            },
            "summary_fields": [
                {
                    "field":    "unknown_scope_total",
                    "label":    "WATCHTOWER Unknown Scope",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "explained_by_operators",
                    "label":    "Attributed to Operators",
                    "format":   "count",
                    "fallback": "—",
                },
                {
                    "field":    "remaining_unknown",
                    "label":    "Still Unknown",
                    "format":   "count_warn_nonzero",
                    "fallback": "—",
                },
                {
                    "field":    "persistent_funders",
                    "label":    "Persistent Funders Found",
                    "format":   "count",
                    "fallback": "—",
                },
            ],
            "warning_rules": [
                {
                    "field":     "remaining_unknown",
                    "condition": "gt_zero",
                    "colour":    "amber",
                },
            ],
            "empty_state": "No outcome intelligence data available.",
        },
    },

    # ── alerts_v1 ────────────────────────────────────────────────────────────
    "alerts_v1": {
        "required": {
            "active_count":            int,
            "active":                  list,
        },
        "optional": {
            "recovered":               list,
            "generated_at":            int,
            "ok":                      bool,
        },
        "allowed_status_values": None,

        "display": {
            "title": "Alerts",
            "primary_metric": {
                "field":    "active_count",
                "label":    "Active Alerts",
                "format":   "count_warn_nonzero",
                "fallback": "0",
            },
            "summary_fields": [
                {
                    "field":    "active_count",
                    "label":    "Active",
                    "format":   "count_warn_nonzero",
                    "fallback": "0",
                },
                {
                    "field":    "active",
                    "label":    "Alert List",
                    "format":   "breakdown_dict",
                    "fallback": "—",
                },
            ],
            "empty_state": "No alert data available.",
        },
    },
}


# ── Framework constants ────────────────────────────────────────────────────────

SUPPORTED_FRAMEWORK_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

VALID_LIFECYCLE_STATES: frozenset[str] = frozenset({
    "REGISTERED",
    "INSTRUMENTED",
    "VALIDATED",
    "ACTIVE",
    "DEPRECATED",
    "ARCHIVED",
})

MINIMUM_CONTRACT_REQUIRED_STATES: frozenset[str] = frozenset({
    "INSTRUMENTED", "VALIDATED", "ACTIVE", "DEPRECATED",
})

VALID_INFRASTRUCTURE_MODELS: frozenset[str] = frozenset({
    "GRAPH", "MESH", "FLAT", "NONE", "CUSTOM",
})

VALID_CAPABILITY_STATES: frozenset[str] = frozenset({
    "SUPPORTED", "UNSUPPORTED", "NOT_DECLARED",
})

KNOWN_CAPABILITIES: frozenset[str] = frozenset({
    "health",
    "infrastructure",
    "discovery_assurance",
    "detection_coverage",
    "failure_attribution",
    "behaviour",
    "intelligence",
    "signal_observatory",
    "outcome_intelligence",
    "alerts",
})


# ── Display metadata accessor ──────────────────────────────────────────────────

def get_display_metadata(contract_name: str) -> dict | None:
    """Return the display metadata for a contract, or None if absent."""
    contract = CAPABILITY_CONTRACTS.get(contract_name)
    if contract is None:
        return None
    return contract.get("display")


# ── Display metadata validation ───────────────────────────────────────────────

def validate_display_metadata(contract_name: str) -> list[str]:
    """Validate the display block of a contract.

    Returns a list of error strings. Called from tests and CLI; never at startup.
    """
    contract = CAPABILITY_CONTRACTS.get(contract_name)
    if contract is None:
        return [f"Unknown contract: {contract_name!r}"]

    display = contract.get("display")
    if display is None:
        return []  # display is optional

    errors: list[str] = []
    all_fields = set(contract.get("required", {})) | set(contract.get("optional", {}))

    def _check_field_ref(field_path: str, context: str) -> None:
        # Only validate top-level field refs (dotted paths are nested payloads,
        # validated at render time when the live payload is available).
        top = field_path.split(".")[0]
        if top not in all_fields:
            errors.append(
                f"{contract_name}: {context} references field {field_path!r} "
                f"which is not declared in required or optional."
            )

    def _check_format(fmt: str, context: str) -> None:
        if fmt not in FORMAT_TYPES:
            errors.append(
                f"{contract_name}: {context} has unknown format {fmt!r}. "
                f"Valid formats: {sorted(FORMAT_TYPES)}."
            )

    # Validate primary_metric
    pm = display.get("primary_metric")
    if pm is not None:
        if not isinstance(pm, dict):
            errors.append(f"{contract_name}: display.primary_metric must be a mapping.")
        else:
            if "field" not in pm:
                errors.append(f"{contract_name}: display.primary_metric missing 'field'.")
            else:
                _check_field_ref(pm["field"], "primary_metric")
            if "format" in pm:
                _check_format(pm["format"], "primary_metric")

    # Validate summary_fields
    for i, sf in enumerate(display.get("summary_fields", [])):
        if not isinstance(sf, dict):
            errors.append(f"{contract_name}: display.summary_fields[{i}] must be a mapping.")
            continue
        if "field" not in sf:
            errors.append(f"{contract_name}: display.summary_fields[{i}] missing 'field'.")
        else:
            _check_field_ref(sf["field"], f"summary_fields[{i}]")
        if "format" in sf:
            _check_format(sf["format"], f"summary_fields[{i}]")

    # Validate status_field
    sf_decl = display.get("status_field")
    if sf_decl is not None:
        if "field" not in sf_decl:
            errors.append(f"{contract_name}: display.status_field missing 'field'.")
        else:
            _check_field_ref(sf_decl["field"], "status_field")
        for val, colour in (sf_decl.get("values") or {}).items():
            if colour not in COLOUR_VALUES:
                errors.append(
                    f"{contract_name}: display.status_field.values[{val!r}] "
                    f"has unknown colour {colour!r}."
                )

    # Validate warning_rules
    for i, wr in enumerate(display.get("warning_rules", [])):
        if "field" not in wr:
            errors.append(f"{contract_name}: display.warning_rules[{i}] missing 'field'.")
        else:
            _check_field_ref(wr["field"], f"warning_rules[{i}]")
        if "colour" in wr and wr["colour"] not in COLOUR_VALUES:
            errors.append(
                f"{contract_name}: display.warning_rules[{i}].colour "
                f"{wr['colour']!r} is unknown."
            )

    return errors


# ── Payload validation ────────────────────────────────────────────────────────

def validate_capability_payload(contract_name: str, payload: dict) -> list[str]:
    """Validate a provider payload against the named contract.

    Returns a list of error strings. An empty list means the payload is valid.
    Does not raise — callers decide whether errors are fatal.
    """
    if contract_name not in CAPABILITY_CONTRACTS:
        return [f"Unknown contract: {contract_name!r}"]

    contract = CAPABILITY_CONTRACTS[contract_name]
    errors: list[str] = []

    for field, expected_type in contract.get("required", {}).items():
        if field not in payload:
            errors.append(f"Missing required field: {field!r}")
            continue
        value = payload[field]
        if not isinstance(value, expected_type):
            type_names = (
                " | ".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            errors.append(
                f"Field {field!r}: expected {type_names}, "
                f"got {type(value).__name__}"
            )

    if contract.get("allowed_status_values") and "status" in payload:
        allowed = contract["allowed_status_values"]
        if payload["status"] not in allowed:
            errors.append(
                f"Field 'status': {payload['status']!r} is not one of {allowed}"
            )

    return errors
