#!/usr/bin/env python3
"""Export a network approval pack from the local Flask API."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def fetch_json(base_url: str, path: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def short_addr(value: str | None) -> str:
    if not value:
        return "-"
    return value if len(value) <= 14 else f"{value[:8]}...{value[-4:]}"


def fmt_num(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "0"


def fmt_sol(value: Any) -> str:
    try:
        amount = float(value or 0)
    except Exception:
        return "-"
    if amount == 0:
        return "0 SOL"
    if abs(amount) < 0.01:
        return f"{amount:.6f} SOL"
    if abs(amount) < 1:
        return f"{amount:.4f} SOL"
    return f"{amount:.2f} SOL"


def fmt_pct(value: Any) -> str:
    try:
        amount = float(value or 0)
    except Exception:
        return "-"
    return f"{amount:.0f}%"


def fmt_money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except Exception:
        return "-"
    if amount <= 0:
        return "-"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:,.0f}"


def fmt_date(ts: Any) -> str:
    if not ts:
        return "-"
    try:
        value = float(ts)
    except Exception:
        return str(ts)
    if value <= 0:
        return "-"
    return time.strftime("%d %b %Y", time.localtime(value))


def clean(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("\r", " ").strip()


def table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_None._\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(clean(cell) for cell in row) + " |")
    return "\n".join(lines) + "\n"


def account_label(account: dict[str, Any] | None) -> str:
    if not account:
        return "-"
    prefix = account.get("type") or "ACCOUNT"
    label = account.get("label") or account.get("category") or "unknown"
    return f"{prefix} {label}"


def get_tagged_creators(conn: sqlite3.Connection | None, network_name: str) -> list[dict[str, Any]]:
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            WITH members AS (
                SELECT DISTINCT creator_address
                FROM network_membership
                WHERE network_name = ?
            ),
            coord AS (
                SELECT creator_address,
                       COUNT(*) AS coord_edges,
                       COUNT(DISTINCT bridge_funder) AS coord_funders
                FROM (
                    SELECT creator_a AS creator_address, bridge_funder
                    FROM coordinated_creator_edges
                    WHERE creator_a IN (SELECT creator_address FROM members)
                      AND bridge_funder NOT IN (SELECT address FROM infra_wallets)
                    UNION ALL
                    SELECT creator_b AS creator_address, bridge_funder
                    FROM coordinated_creator_edges
                    WHERE creator_b IN (SELECT creator_address FROM members)
                      AND bridge_funder NOT IN (SELECT address FROM infra_wallets)
                )
                GROUP BY creator_address
            ),
            explicit_tags AS (
                SELECT creator_address,
                       GROUP_CONCAT(tag, ', ') AS tags
                FROM creator_tags
                WHERE creator_address IN (SELECT creator_address FROM members)
                  AND (
                    lower(tag) LIKE '%self%'
                    OR lower(tag) LIKE '%coord%'
                  )
                GROUP BY creator_address
            )
            SELECT
                m.creator_address,
                COALESCE(csf.is_self_funding, 0) AS is_self_funding,
                COALESCE(csf.self_funding_percentage, 0) AS self_funding_percentage,
                COALESCE(csf.self_funding_intermediates, 0) AS self_funding_intermediates,
                COALESCE(csf.total_funders, 0) AS total_funders,
                COALESCE(coord.coord_edges, 0) AS coord_edges,
                COALESCE(coord.coord_funders, 0) AS coord_funders,
                explicit_tags.tags
            FROM members m
            LEFT JOIN creator_self_funding csf
                ON csf.creator_address = m.creator_address
            LEFT JOIN coord
                ON coord.creator_address = m.creator_address
            LEFT JOIN explicit_tags
                ON explicit_tags.creator_address = m.creator_address
            WHERE COALESCE(csf.is_self_funding, 0) = 1
               OR COALESCE(coord.coord_edges, 0) > 0
               OR explicit_tags.tags IS NOT NULL
            ORDER BY
                COALESCE(csf.is_self_funding, 0) DESC,
                COALESCE(coord.coord_edges, 0) DESC,
                m.creator_address
        """, (network_name,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def render_network(
    row: dict[str, Any],
    detail: dict[str, Any],
    coord: dict[str, Any] | None = None,
    tagged_creators: list[dict[str, Any]] | None = None,
) -> str:
    name = detail.get("network_name") or row.get("network_name")
    display = detail.get("display_name") or row.get("display_name") or name
    g = detail.get("g_distribution") or {}
    known = g.get("known") or {}
    g_history = ", ".join(f"{grade}: {count}" for grade, count in sorted(known.items())) or "-"
    unknown = g.get("unknown") or 0
    tracked = detail.get("tracked_token_count")
    migrated = detail.get("token_count") or 0
    bonding = detail.get("bonding_curve_count") or 0

    lines: list[str] = []
    lines.append(f"## {display}")
    lines.append("")
    lines.append(f"- Internal ID: `{name}`")
    lines.append(f"- Review state: `{row.get('review_state', '-')}` / status `{row.get('status', '-')}`")
    lines.append(f"- Suggested: {row.get('suggested_label', '-')} - {row.get('suggested_reason', '-')}")
    lines.append(f"- Type: `{detail.get('network_type') or row.get('network_type') or 'unknown'}`")
    lines.append(f"- Grouped by: {detail.get('display_name_reason') or row.get('display_name_reason') or '-'}")
    lines.append(f"- Dominant source: `{detail.get('display_name_source') or row.get('display_name_source') or '-'}`")
    lines.append(f"- Network members: {fmt_num(detail.get('network_size') or detail.get('creators_count'))}")
    lines.append(f"- Creators with tokens: {fmt_num(detail.get('creators_with_tokens'))}")
    lines.append(f"- Tracked tokens: {fmt_num(tracked if tracked is not None else migrated + bonding)}")
    lines.append(f"- Migrated tokens: {fmt_num(migrated)}")
    lines.append(f"- Bonding curve / non-migrated: {fmt_num(bonding)}")
    lines.append(f"- Best observed peak: {fmt_money(g.get('best_peak_mc'))} {g.get('best_token_class') or ''}".strip())
    lines.append(f"- G history: {g_history}" + (f" ({unknown} unknown excluded)" if unknown else ""))
    if coord:
        lines.append(
            f"- Coordination edges: {fmt_num(coord.get('total_edges'))} "
            f"({fmt_num(coord.get('intra_count'))} intra-network, "
            f"{fmt_num(coord.get('outside_creator_count'))} outside creators, "
            f"{fmt_num(coord.get('ignored_edge_count'))} infra ignored)"
        )
    lines.append("")

    if coord:
        lines.append("### Coordination / Connection Breakdown")
        lines.append(table(
            ["Funder", "Label", "Members", "Intra Edges", "Outside Creators", "Total Edges"],
            [
                [
                    item.get("funder", "-"),
                    item.get("funder_label") or item.get("funder_type") or "-",
                    item.get("member_count", 0),
                    item.get("intra_edges", 0),
                    item.get("outside_creators", 0),
                    item.get("total_edges", 0),
                ]
                for item in coord.get("bridge_funders", [])
            ],
        ))

        ignored = coord.get("ignored_bridge_funders", [])
        if ignored:
            lines.append("### Ignored Infrastructure Edges")
            lines.append(table(
                ["Account", "Label", "Type", "Historical Edges", "Reason"],
                [
                    [
                        item.get("funder", "-"),
                        item.get("funder_label") or "-",
                        item.get("funder_type") or "INFRA",
                        item.get("total_edges", 0),
                        "Ignored for coordination analysis",
                    ]
                    for item in ignored
                ],
            ))

        lines.append("### Outside Creator Reach")
        lines.append(table(
            ["Outside Creator", "Edges", "Via Funders"],
            [
                [
                    item.get("creator", "-"),
                    item.get("edge_count", 0),
                    ", ".join(item.get("via_funders") or []),
                ]
                for item in coord.get("top_outside_creators", [])
            ],
        ))

    lines.append("### CEX / INFRA Funding Touchpoints")
    lines.append(table(
        ["Type", "Label", "Category", "Address", "Description"],
        [
            [
                item.get("type", "-"),
                item.get("label", "-"),
                item.get("category", "-"),
                item.get("address", "-"),
                item.get("description", "-"),
            ]
            for item in detail.get("cex_infra_accounts", [])
        ],
    ))

    lines.append("### Other Direct Wallet Funders")
    lines.append(table(
        ["Wallet", "Creators", "Total Funded", "Latest Seen"],
        [
            [
                item.get("funder_address", "-"),
                item.get("creator_count", 0),
                fmt_sol(item.get("total_sol")),
                item.get("latest_seen_at", "-"),
            ]
            for item in detail.get("other_direct_funders", [])
        ],
    ))

    lines.append("### Funder To Creator Evidence")
    creator_best = {
        item.get("creator"): item
        for item in detail.get("creator_summaries", [])
    }
    lines.append(table(
        ["Funder", "Creator", "Funded", "Best", "Peak"],
        [
            [
                edge.get("funder_address", "-"),
                edge.get("creator_address", "-"),
                fmt_sol(edge.get("amount_sol")),
                (creator_best.get(edge.get("creator_address")) or {}).get("best_token_class") or "G?",
                fmt_money((creator_best.get(edge.get("creator_address")) or {}).get("best_peak_mc")),
            ]
            for edge in detail.get("funder_edges", [])
        ],
    ))

    lines.append("### Secondary Accounts / Operator Fingerprints")
    lines.append(table(
        ["Type", "Label", "Confidence", "Creators", "Main Funder", "Pattern", "Amount"],
        [
            [
                sig.get("type", "-"),
                sig.get("label", "-"),
                sig.get("confidence", "-"),
                sig.get("creator_count", 0),
                sig.get("primary_funder", "-"),
                account_label(sig.get("secondary_classification")) if sig.get("type") == "shared_jito_tip_pattern" else sig.get("secondary_address", "-"),
                fmt_sol(sig.get("tip_amount_sol")) if sig.get("tip_amount_sol") is not None else sig.get("amount_band", "-"),
            ]
            for sig in detail.get("operator_signatures", [])
        ],
    ))

    tagged_creators = tagged_creators or []
    lines.append("### Tagged Creators")
    lines.append(table(
        ["Creator", "Tags", "Self Funding", "Self Path", "Coord Edges", "Coord Funders"],
        [
            [
                item.get("creator_address", "-"),
                ", ".join(
                    tag for tag in [
                        "SELF-FUND" if item.get("is_self_funding") else "",
                        "COORD" if item.get("coord_edges") else "",
                        item.get("tags") or "",
                    ]
                    if tag
                ) or "-",
                fmt_pct(item.get("self_funding_percentage")),
                f"{item.get('self_funding_intermediates', 0)}/{item.get('total_funders', 0)}",
                item.get("coord_edges", 0),
                item.get("coord_funders", 0),
            ]
            for item in tagged_creators
        ],
    ))

    lines.append("### Creator Outcomes")
    lines.append(table(
        ["Creator", "Tokens", "Funded", "Best", "Peak", "Latest", "LIQ"],
        [
            [
                item.get("creator", "-"),
                item.get("token_count", 0),
                fmt_sol(item.get("total_funded_sol")),
                item.get("best_token_class") or "G?",
                fmt_money(item.get("best_peak_mc")),
                fmt_date(item.get("latest_seen_at")),
                item.get("liquidity_removed_count", 0),
            ]
            for item in detail.get("creator_summaries", [])
        ],
    ))

    lines.append("### Migrated Token Timeline")
    lines.append(table(
        ["Date", "Token", "Mint", "Class", "Peak", "Creator", "LIQ"],
        [
            [
                fmt_date(token.get("migrated_at")),
                token.get("symbol") or token.get("name") or "-",
                token.get("mint", "-"),
                token.get("token_class") or "G?",
                fmt_money(token.get("market_cap_highest") or token.get("market_cap")),
                token.get("creator", "-"),
                "yes" if token.get("liquidity_removed") else "",
            ]
            for token in detail.get("tokens", [])
        ],
    ))

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5002")
    parser.add_argument("--output", default="docs/NETWORK_APPROVAL_DOCUMENT.md")
    parser.add_argument("--db", default="database/flex_complete_database.db")
    args = parser.parse_args()

    approval = fetch_json(args.base_url, "/api/network-approval/list")
    networks = approval.get("networks") or []
    output = Path(args.output)
    db_conn = sqlite3.connect(args.db) if args.db else None

    sections = [
        "# Network Approval Document",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Source: `{args.base_url}`",
        f"Networks: {len(networks)}",
        "",
        "This document is a point-in-time approval pack generated from the local graph APIs. Canonical network IDs are preserved.",
        "",
        "## Queue Summary",
        table(["State", "Count"], [[key, value] for key, value in sorted((approval.get("counts") or {}).items())]),
    ]

    for index, row in enumerate(networks, start=1):
        name = row.get("network_name")
        if not name:
            continue
        encoded = urllib.parse.quote(str(name), safe="")
        try:
            detail = fetch_json(args.base_url, f"/api/network-tokens/{encoded}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            detail = {"network_name": name, "display_name": row.get("display_name"), "error": str(exc)}
        try:
            coord = fetch_json(args.base_url, f"/api/network-coord/{encoded}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            coord = {}
        tagged_creators = get_tagged_creators(db_conn, str(name))
        sections.append(f"\n<!-- network {index}/{len(networks)} -->")
        if detail.get("error"):
            sections.append(f"## {row.get('display_name') or name}\n\nError: {detail['error']}\n")
        else:
            sections.append(render_network(row, detail, coord, tagged_creators))

    output.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    if db_conn is not None:
        db_conn.close()
    print(f"Wrote {output} ({len(networks)} networks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
