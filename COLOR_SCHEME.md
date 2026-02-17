# Unified Color Scheme for Flex Project

## Overview
All colors in the Flex project are now defined using CSS custom properties (variables) in the `:root` selector in `main.py`. This ensures consistency throughout the application.

## CSS Custom Properties Reference

### Primary Colors (SolanaFM Professional Dark)
```css
--primary: #7c3aed;           /* Main purple accent - UI elements, borders */
--primary-dark: #6d28d9;      /* Darker purple - hover states */
--primary-light: rgba(124, 58, 237, 0.15);  /* Purple overlay */
```

### Text Colors (Professional Gray Scale)
```css
--text-primary: #e5e7eb;      /* Main text color - light gray */
--text-secondary: #9ca3af;    /* Muted/secondary text - medium gray */
--text-dark: #1a1a1a;         /* Dark text on light backgrounds */
--text-light: #f3f4f6;        /* Light text on dark backgrounds */
```

### Risk & Reuse Levels (Professional Vibrant)
```css
--color-critical: #ef4444;    /* CRITICAL risk - bright red */
--color-high: #f97316;        /* HIGH risk / Strong coordination - orange */
--color-medium: #eab308;      /* MEDIUM risk / Shared - yellow */
--color-low: #22c55e;         /* LOW risk / Weak - green */
--color-none: #3b82f6;        /* NONE / Independent - blue */
```

### Background Colors (SolanaFM Dark Navy)
```css
--bg-primary: #0f172a;        /* Primary background - dark navy */
--bg-secondary: rgba(15, 23, 42, 0.8);  /* Secondary background (cards) */
--bg-overlay: rgba(124, 58, 237, 0.1);  /* Purple overlay */
```

### Accent Colors (SolanaFM Vibrant)
```css
--accent-cyan: #06b6d4;       /* Cyan - vibrant cyan */
--accent-green: #10b981;      /* Green - vibrant green */
--accent-purple: #a855f7;     /* Purple - vibrant purple */
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
**Status**: SolanaFM professional theme implemented
**Theme**: Dark navy background with vibrant purple accents - professional, clean, modern
