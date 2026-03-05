# Flex Application Color Scheme Guide

## Overview

The application now uses a unified color scheme inspired by the webhook monitor design. This guide helps maintain consistency across all pages.

## Color Palette

### Primary Colors
| Color | Hex | Usage |
|-------|-----|-------|
| Purple | `#a78bfa` | Headers, section titles, labels, addresses, links in some contexts |
| Cyan | `#06b6d4` | Links, buttons, active elements, interactive components |
| Dark Green | `#16a34a` | Positive values, amounts, success status, active badges |
| Yellow | `#fbbf24` | Creator highlights, special emphasis |

### Supporting Colors
| Color | Hex | Usage |
|-------|-----|-------|
| Text Primary | `#e5e7eb` | Main text |
| Text Secondary | `#9ca3af` | Secondary text, timestamps |
| Background | `#0a0a0e` to `#0d0d15` | Main background gradient |
| Card Background | `rgba(30, 30, 40, 0.8)` | Cards, tables, panels |
| Border | `rgba(167, 139, 250, 0.3)` | Card borders, dividers |
| Hover | `rgba(167, 139, 250, 0.05)` | Hover background |

### Risk/Status Colors
| Level | Background | Text | Usage |
|-------|-----------|------|-------|
| Low | `rgba(21, 128, 61, 0.2)` | `#16a34a` | Low risk, success |
| Medium | `rgba(234, 179, 8, 0.2)` | `#eab308` | Medium risk, warning |
| High | `rgba(239, 68, 68, 0.2)` | `#ef4444` | High risk, error |

## CSS Variables

All pages should use these CSS variables in their `:root` block:

```css
:root {
    --color-purple: #a78bfa;
    --color-cyan: #06b6d4;
    --color-green: #16a34a;
    --color-yellow: #fbbf24;

    --text-primary: #e5e7eb;
    --text-secondary: #9ca3af;

    --bg-dark: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%);
    --bg-card: rgba(30, 30, 40, 0.8);
    --bg-hover: rgba(167, 139, 250, 0.05);

    --border-color: rgba(167, 139, 250, 0.3);
    --border-hover: rgba(167, 139, 250, 0.6);
}
```

## Implementation Checklist

When updating a page, ensure:

- [ ] Headers use `--color-purple`
- [ ] Links use `--color-cyan`
- [ ] Amounts/positive values use `--color-green`
- [ ] Creator names use `--color-yellow`
- [ ] Card backgrounds use `--bg-card`
- [ ] Card borders use `--border-color`
- [ ] Hover states use `--border-hover` and `--bg-hover`
- [ ] Status badges follow risk color scheme
- [ ] Text uses `--text-primary` or `--text-secondary`

## Pages Updated

✅ `/webhook-monitor` - Complete (original design source)
✅ Main Dashboard (`/`) - HTML_TEMPLATE updated with CSS variables

## Pages Pending Update

The following pages need color scheme updates. They still use legacy colors:

1. `/coordinated-funders` - `coordinated_funders_view()` (line 9377)
2. `/clusters` - `clusters_dashboard()` (line 9141)
3. `/networks` - `networks_dashboard()` (line 13186)
4. `/top-funding-hubs` - `top_funding_hubs()` (line 13805)
5. `/funder-details/<address>` - `funder_details_view()` (line 10641)
6. `/funding-hub/<address>` - `funding_hub()` (line 10995)
7. Individual creator/network pages

## Quick Migration Template

For each page's HTML template in main.py, replace the `<style>` section:

### Old Pattern
```html
<style>
    /* Scattered hardcoded colors */
    body { background: #0f0f1e; }
    a { color: #3b82f6; }
    .stat-value { color: #22c55e; }
</style>
```

### New Pattern
```html
<style>
    :root {
        --color-purple: #a78bfa;
        --color-cyan: #06b6d4;
        --color-green: #16a34a;
        --color-yellow: #fbbf24;
        --text-primary: #e5e7eb;
        --text-secondary: #9ca3af;
        --bg-card: rgba(30, 30, 40, 0.8);
        --border-color: rgba(167, 139, 250, 0.3);
    }

    body {
        background: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%);
        color: var(--text-primary);
    }

    a { color: var(--color-cyan); }
    .stat-value { color: var(--color-cyan); } /* Or --color-green if it's an amount */
    .card {
        background: var(--bg-card);
        border: 1px solid var(--border-color);
    }
</style>
```

## Key Color Replacements

When updating pages, use these replacements:

| Old Color | New Variable | Notes |
|-----------|--------------|-------|
| `#22c55e` | `--color-green` | Light green → dark green (better contrast) |
| `#7c3aed` | `--color-purple` | Purple adjustments for consistency |
| `#06b6d4` | `--color-cyan` | Cyan already correct |
| `#3b82f6` | `--color-cyan` | Blue links → cyan |
| `#fbbf24` | `--color-yellow` | Yellow already correct |
| Hardcoded backgrounds | `--bg-card` | Use variable instead |
| Border colors | `--border-color` | Use variable instead |

## Testing Checklist

After updating a page:

1. Check headers are purple
2. Check links are cyan
3. Check amounts/values are correct color
4. Check card backgrounds match
5. Check hover states work
6. Check status badges display correctly
7. Check text contrast (should be #e5e7eb on dark backgrounds)
8. Check in different browser zoom levels

## Global Styles CSS File

A `global_styles.css` file has been created with reusable classes:

- `.card`, `.metric-card`, `.stat-card` - Card styling
- `.label`, `.metric-label` - Label styling
- `.amount`, `.amount-positive` - Amount styling
- `.address`, `.mint`, `.tx-hash` - Monospace code styling
- `.status-active`, `.status-idle`, etc. - Status badges
- `.creator`, `.creator-address` - Creator highlights
- Button and link styling

This file can be included in pages that use standard components:
```html
<link rel="stylesheet" href="/static/global_styles.css">
```

## Notes

- The webhook monitor (`/webhook-monitor`) was the inspiration for this color scheme
- All pages should now follow this design system
- Font family should be `'Courier New', monospace` for code/addresses
- Use CSS variables instead of hardcoded colors for easier maintenance
- Dark green (`#16a34a`) is deliberately darker than the original (`#22c55e`) for better contrast on the dark background
