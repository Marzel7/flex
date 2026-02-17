# Unified Color Scheme for Flex Project

## Overview
All colors in the Flex project are now defined using CSS custom properties (variables) in the `:root` selector in `main.py`. This ensures consistency throughout the application.

## CSS Custom Properties Reference

### Primary Colors (Neutral Gray Base)
```css
--primary: #4a4a52;           /* Main neutral gray - UI elements, borders */
--primary-dark: #383840;      /* Darker shade - hover states */
--primary-light: rgba(74, 74, 82, 0.1);  /* Subtle overlay */
```

### Text Colors (Uniform Gray Scale)
```css
--text-primary: #a8a8b0;      /* Main text color - medium gray */
--text-secondary: #787880;    /* Muted/secondary text - darker gray */
--text-dark: #1a1a1a;         /* Dark text on light backgrounds */
--text-light: #c8c8d0;        /* Light text on dark backgrounds */
```

### Risk & Reuse Levels (Monochrome with Subtle Variance)
```css
--color-critical: #6f6f75;    /* CRITICAL risk - darker gray */
--color-high: #717177;        /* HIGH risk / Strong coordination - gray */
--color-medium: #737979;      /* MEDIUM risk / Shared - neutral gray */
--color-low: #757b7f;         /* LOW risk / Weak - light gray */
--color-none: #717d75;        /* NONE / Independent - cool gray */
```

### Background Colors (Very Dark Grays)
```css
--bg-primary: #0a0a10;        /* Primary background - nearly black */
--bg-secondary: rgba(0, 0, 0, 0.7);  /* Secondary background (cards) - very dark */
--bg-overlay: rgba(74, 74, 82, 0.06);  /* Very subtle overlay */
```

### Accent Colors (Subtle Gray Variations)
```css
--accent-cyan: #4a7a88;       /* Cyan - muted cool gray */
--accent-green: #5a7a6a;      /* Green - muted gray-green */
--accent-purple: #6a6a7a;     /* Purple - neutral gray */
```

## Creator Pool Tag Color Mapping

### Tag → Reuse Level → Color

| Tag | Reuse Level | Semantic Level | Color | Hex Code | Use Case |
|-----|-------------|---|--------|----------|----------|
| **INDEPENDENT** | NONE | Low Risk | Green | `--color-none` (#6bcf7f) | No creator reuse, isolated operation |
| **CREATOR_POOL_WEAK** | LOW | Low-Medium Risk | Yellow | `--color-low` (#ffd93d) | Minimal creator reuse, weak signal |
| **CREATOR_POOL_SHARED** | MEDIUM | Medium Risk | Orange | `--color-medium` (#ffa94d) | Moderate creator reuse, coordinated |
| **CREATOR_POOL_STRONG** | HIGH | High Risk | Red | `--color-high` (#ff6b6b) | Strong creator reuse, coordinated |

## Risk Level Color Mapping

| Risk Level | Color | Hex Code | Usage |
|-----------|-------|----------|-------|
| CRITICAL | Red | `--color-critical` (#ef4444) | Highest risk tokens |
| HIGH | Red | `--color-high` (#ff6b6b) | High risk tokens |
| MEDIUM | Orange | `--color-medium` (#ffa94d) | Medium risk tokens |
| LOW | Yellow | `--color-low` (#ffd93d) | Low risk tokens |

## Implementation Guidelines

### Using CSS Variables in Styles
```html
<!-- Instead of hardcoding colors -->
<div style="color: var(--primary); background: var(--bg-secondary);">

<!-- For hover effects -->
<div onmouseover="this.style.background='var(--primary-light)'">
```

### Text Contrast Rules
- **Dark text on light backgrounds**: Use `--text-dark` (#1a1a1a)
- **Light text on dark backgrounds**: Use `--text-light` (#ffffff) or `--text-primary` (#e0e0e0)
- **Secondary/muted text**: Use `--text-secondary` (#a0a0a0)

### Border Styling
- **All borders**: Use `--primary` (#6366f1) unless semantic color required
- **Danger borders**: Use `--color-critical` or `--color-high`
- **Success borders**: Use `--color-none`

### Background Styling
- **Cards/panels**: Use `--bg-secondary` with `--primary` borders
- **Overlays/hover**: Use `--bg-overlay` for visual feedback
- **Primary background**: Use `--bg-primary` for full page sections

## Color Consistency Checklist

When adding new UI elements:
- [ ] Does it use a CSS variable from `:root`?
- [ ] Is the text color appropriate for the background?
- [ ] Does the color match the semantic meaning (risk, status, etc.)?
- [ ] Are hover states using `--primary-light` or darkened variants?
- [ ] Are risk/reuse tags using the correct semantic color?

## Example: Adding a New Component

```javascript
// Bad - Hardcoded colors
html += '<div style="color: #6366f1; background: rgba(0, 0, 0, 0.3);">';

// Good - Using CSS variables
html += '<div style="color: var(--primary); background: var(--bg-secondary);">';

// Better - For semantic colors (risk levels)
const riskColor = cluster.risk_level === 'CRITICAL' ? 'var(--color-critical)' :
                  cluster.risk_level === 'HIGH' ? 'var(--color-high)' :
                  cluster.risk_level === 'MEDIUM' ? 'var(--color-medium)' : 'var(--color-low)';
html += `<div style="color: ${riskColor};">`;
```

## Migration Notes

All existing hardcoded colors in `main.py` have been migrated to use CSS variables:
- `#6366f1` → `var(--primary)`
- `#a0a0a0` → `var(--text-secondary)`
- `#e0e0e0` → `var(--text-primary)`
- `rgba(99, 102, 241, 0.15)` → `var(--primary-light)`
- Risk level colors mapped to semantic variables
- Tag colors standardized across definitions and rendering

This ensures that:
1. **Consistency**: Same color used for same semantic meaning
2. **Maintainability**: Change one variable to update all instances
3. **Accessibility**: All colors maintain proper contrast ratios
4. **Scalability**: Easy to add new color themes by overriding `:root` variables

---
**Last Updated**: 2026-02-17
**Status**: Monochrome dark theme implemented
**Theme**: Ultra-dark monochrome gray palette - no bright colors, completely uniform
