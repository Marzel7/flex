"""
Operations OS — Generic Shell Routes.

Provides:
  GET /ops-os             — Operations index (all registered operations)
  GET /ops-os/<op_id>     — Generic operation page
  GET /api/ops-os/registry — Registry metadata (JSON)
  GET /api/ops-os/<op_id>/capabilities — Resolved capabilities (JSON)

Rules:
  - The shell knows nothing about WATCHTOWER, treasuries, subproviders,
    wrap-closes, or pump.fun.
  - All operation-specific terms come from the operation's vocabulary declaration.
  - All data comes through registered capability providers.
  - The shell never calls raw URLs or reads WATCHTOWER-specific tables.
  - Contract validation runs before every capability render.
"""

from __future__ import annotations

import time
import urllib.request
import urllib.error
import json
from typing import Any

from flask import Blueprint, render_template, jsonify, abort, request

from src.ops.registry_cache import get_registry
from src.ops.capability_resolver import resolve_all_capabilities, CapabilityResult
from src.ops.contracts import KNOWN_CAPABILITIES, get_display_metadata

ops_shell_bp = Blueprint("ops_shell", __name__)


# ── Internal HTTP fetcher (injected into capability resolver) ─────────────────
# Uses only stdlib so there is no new dependency.
# Calls are made within the Gunicorn worker to the same Flask app on localhost.

def _make_http_fetcher(base_url: str):
    """Return a fetcher that calls base_url + path and parses JSON."""
    def fetcher(path: str) -> dict:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
        except Exception as exc:
            raise RuntimeError(f"Fetch error for {url}: {exc}") from exc
    return fetcher


def _local_fetcher(path: str) -> dict:
    """Fetches from the local Flask app on port 5002 (default Gunicorn port)."""
    import os
    port = os.environ.get("OPS_SHELL_LOCAL_PORT", "5002")
    base = f"http://127.0.0.1:{port}"
    return _make_http_fetcher(base)(path)


# ── Registry API ──────────────────────────────────────────────────────────────

@ops_shell_bp.route("/api/ops-os/registry")
def api_ops_registry():
    """Return metadata for all registered operations."""
    try:
        registry = get_registry()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    ops = []
    for op_id in sorted(registry):
        op = registry[op_id]
        ops.append({
            "operation_id":            op.operation_id,
            "display_name":            op.display_name,
            "status":                  op.status,
            "infrastructure_model":    op.infrastructure_model,
            "framework_schema_version": op.framework_schema_version,
            "definition_version":      op.definition_version,
            "operation_version":       op.operation_version,
            "supported_capabilities":  op.supported_capabilities(),
        })

    return jsonify({
        "ok":          True,
        "generated_at": int(time.time()),
        "count":        len(ops),
        "operations":   ops,
    })


# ── Capabilities API ──────────────────────────────────────────────────────────

@ops_shell_bp.route("/api/ops-os/<operation_id>/capabilities")
def api_ops_capabilities(operation_id: str):
    """Return resolved + validated capability payloads for one operation."""
    try:
        registry = get_registry()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    if operation_id not in registry:
        return jsonify({"ok": False, "error": f"Unknown operation: {operation_id!r}"}), 404

    op = registry[operation_id]
    results = resolve_all_capabilities(op, http_fetcher=_local_fetcher)

    caps_out = {}
    for cap_name, result in results.items():
        caps_out[cap_name] = {
            "state":          result.state,
            "render_status":  result.render_status,
            "contract":       result.contract,
            "provider_id":    result.provider_id,
            "provider_path":  result.provider_path,
            "contract_errors": result.contract_errors,
            "fetch_error":    result.fetch_error,
            "payload":        result.payload,
        }

    return jsonify({
        "ok":            True,
        "generated_at":  int(time.time()),
        "operation_id":  operation_id,
        "capabilities":  caps_out,
    })


# ── HTML pages ────────────────────────────────────────────────────────────────

@ops_shell_bp.route("/ops-os")
def page_ops_index():
    """Operations OS index — all registered operations."""
    return render_template("ops_shell_index.html", active_page="ops_os_index")


@ops_shell_bp.route("/ops-os/<operation_id>")
def page_ops_operation(operation_id: str):
    """Generic operation page — rendered entirely from the registry definition."""
    try:
        registry = get_registry()
    except Exception as exc:
        # Registry load failure — don't crash, show an error page
        return render_template(
            "ops_shell_operation.html",
            active_page="ops_os",
            operation=None,
            error=str(exc),
        ), 500

    if operation_id not in registry:
        abort(404)

    op = registry[operation_id]

    # Resolve capabilities that are SUPPORTED — fetch payloads and validate contracts.
    # NOT_DECLARED / UNSUPPORTED capabilities are resolved without HTTP calls.
    results = resolve_all_capabilities(op, http_fetcher=_local_fetcher)

    # Build a serialisable summary for the template (avoids passing Python objects to Jinja)
    cap_summary = _build_cap_summary(op, results)

    return render_template(
        "ops_shell_operation.html",
        active_page="ops_os",
        operation=_op_to_dict(op),
        cap_summary=cap_summary,
        error=None,
    )


# ── Template helpers ──────────────────────────────────────────────────────────

def _op_to_dict(op) -> dict:
    """Convert an OperationDefinition to a plain dict safe for Jinja."""
    return {
        "operation_id":          op.operation_id,
        "display_name":          op.display_name,
        "status":                op.status,
        "infrastructure_model":  op.infrastructure_model,
        "framework_schema_version": op.framework_schema_version,
        "definition_version":    op.definition_version,
        "operation_version":     op.operation_version,
        "vocabulary":            op.vocabulary,
        "assurance_mapping":     op.assurance_mapping,
        "supported_capabilities": op.supported_capabilities(),
        "description":           op.raw().get("description", ""),
        "owner":                 op.raw().get("owner", ""),
        "graph":                 op.raw().get("graph"),
    }


def _build_cap_summary(op, results: dict[str, CapabilityResult]) -> list[dict]:
    """Build a list of capability card dicts from resolved results."""
    caps_raw = op.capabilities
    rows = []
    # Canonical display order
    display_order = [
        "health",
        "infrastructure",
        "discovery_assurance",
        "detection_coverage",
        "failure_attribution",
        "alerts",
        "behaviour",
        "intelligence",
        "signal_observatory",
        "outcome_intelligence",
    ]
    seen = set()
    for cap_name in display_order:
        seen.add(cap_name)
        result = results.get(cap_name)
        rows.append(_result_to_card(cap_name, result, caps_raw.get(cap_name)))

    # Any extra capabilities not in the canonical order
    for cap_name in sorted(results):
        if cap_name not in seen:
            result = results[cap_name]
            rows.append(_result_to_card(cap_name, result, caps_raw.get(cap_name)))

    return rows


def _resolve_field(payload: dict | None, field_path: str) -> Any:
    """Resolve a dotted field path against a payload dict.

    Returns the value, or None if any segment is missing.
    Example: "summary.attribution_rate_pct" → payload["summary"]["attribution_rate_pct"]
    """
    if payload is None:
        return None
    parts = field_path.split(".")
    node = payload
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def _build_rendered_fields(
    display: dict | None,
    payload: dict | None,
) -> dict:
    """Convert display metadata + live payload into a renderer-ready dict.

    Returns a dict the template can consume with zero conditional logic.
    All field resolution and formatting decisions are made here in Python,
    not in the template.
    """
    if display is None or payload is None:
        return {"available": False, "fields": [], "primary": None, "status_colour": None}

    def _format_value(raw, fmt: str, fallback: str) -> dict:
        """Return a render descriptor: {value, format, colour_hint}."""
        if raw is None:
            return {"raw": None, "display": fallback, "format": fmt, "colour": "dim"}
        return {"raw": raw, "display": raw, "format": fmt, "colour": "default"}

    # Status colour mapping
    status_colour = None
    sf = display.get("status_field")
    if sf:
        status_val = _resolve_field(payload, sf["field"])
        status_colour = (sf.get("values") or {}).get(str(status_val), "default")

    # Warning rule evaluation — produces {field_path: colour} overrides
    warn_overrides: dict[str, str] = {}
    for rule in display.get("warning_rules", []):
        field_val = _resolve_field(payload, rule["field"])
        condition = rule.get("condition", "gt_zero")
        triggered = False
        if condition == "gt_zero":
            triggered = isinstance(field_val, (int, float)) and field_val > 0
        elif condition == "equals":
            triggered = field_val == rule.get("compare")
        elif condition == "not_equals":
            triggered = field_val != rule.get("compare")
        if triggered:
            warn_overrides[rule["field"]] = rule.get("colour", "red")

    # Primary metric
    primary = None
    pm = display.get("primary_metric")
    if pm:
        raw = _resolve_field(payload, pm["field"])
        primary = {
            "label":   pm.get("label", pm["field"]),
            "raw":     raw,
            "display": raw if raw is not None else pm.get("fallback", "—"),
            "format":  pm.get("format", "plain"),
            "colour":  warn_overrides.get(pm["field"], status_colour or "default"),
        }

    # Summary fields
    fields = []
    for sf_def in display.get("summary_fields", []):
        field_path = sf_def["field"]
        raw = _resolve_field(payload, field_path)
        colour = warn_overrides.get(field_path, "default")
        fields.append({
            "label":   sf_def.get("label", field_path),
            "raw":     raw,
            "display": raw if raw is not None else sf_def.get("fallback", "—"),
            "format":  sf_def.get("format", "plain"),
            "colour":  colour,
        })

    return {
        "available":     True,
        "title":         display.get("title"),
        "primary":       primary,
        "fields":        fields,
        "status_colour": status_colour,
        "empty_state":   display.get("empty_state", "No data available."),
    }


def _result_to_card(cap_name: str, result: CapabilityResult | None, decl: dict | None) -> dict:
    """Convert a CapabilityResult into a Jinja-safe card dict.

    Includes pre-resolved display_data so the template needs no
    capability-specific conditional logic.
    """
    contract_name = None
    display_meta = None

    if result is not None:
        contract_name = result.contract
        if contract_name:
            display_meta = get_display_metadata(contract_name)

    rendered = _build_rendered_fields(
        display_meta,
        result.payload if result else None,
    )

    default_label = cap_name.replace("_", " ").title()
    # Contract's display title takes precedence over the derived label
    label = (display_meta or {}).get("title", default_label) if display_meta else default_label

    if result is None:
        return {
            "name":              cap_name,
            "label":             label,
            "state":             "NOT_DECLARED",
            "render_status":     "NOT_DECLARED",
            "contract":          None,
            "provider_id":       None,
            "provider_path":     None,
            "contract_errors":   [],
            "fetch_error":       None,
            "payload":           None,
            "unsupported_reason": None,
            "display":           rendered,
        }

    return {
        "name":              cap_name,
        "label":             label,
        "state":             result.state,
        "render_status":     result.render_status,
        "contract":          result.contract,
        "provider_id":       result.provider_id,
        "provider_path":     result.provider_path,
        "contract_errors":   result.contract_errors,
        "fetch_error":       result.fetch_error,
        "payload":           result.payload,
        "unsupported_reason": (decl or {}).get("reason") if result.state == "UNSUPPORTED" else None,
        "display":           rendered,
    }


# ── Blueprint registration helper ────────────────────────────────────────────

def register_ops_shell_routes(app):
    app.register_blueprint(ops_shell_bp)
