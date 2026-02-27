# Phase 4B Code Reference - UI Integration

**File**: main.py
**Status**: ✅ All changes implemented and syntax validated

---

## 1. Networks Dashboard - Batch Score Query (new_path)

**Location**: `networks_dashboard()` → `new_path()` function

```python
# Batch fetch all network scores to avoid N+1 queries
network_names = [n['network_name'] for n in all_networks]
scores_map = {}
if network_names:
    try:
        conn, cursor = get_db_conn()
        placeholders = ','.join(['?' for _ in network_names])
        cursor.execute(f'''
            SELECT network_name, score, score_components_json
            FROM network_scores
            WHERE network_name IN ({placeholders})
        ''', network_names)
        for row in cursor.fetchall():
            scores_map[row['network_name']] = {
                'score': row['score'],
                'components': json.loads(row['score_components_json']) if row['score_components_json'] else {}
            }
        conn.close()
    except Exception as e:
        print(f"[DEBUG] Error fetching network scores: {e}")
```

---

## 2. Networks Dashboard - Score Addition to Network Object

**Location**: Inside network loop in `networks_dashboard()` → `new_path()`

```python
# Get score information
score_info = scores_map.get(network_name)

networks.append({
    'name': network_name,
    'tier': network.get('network_type', 'N/A'),
    'is_cex': funder_is_cex,
    'cex_label': cex_label,
    'token_count': token_count,
    'creators_funded': creators_funded,
    'sol_amount': sol_amount,
    'score': score_info['score'] if score_info else None,
    'score_badge': 'high' if (score_info and score_info['score'] >= 70) else ('medium' if (score_info and score_info['score'] >= 30) else 'low') if score_info else None
})
```

---

## 3. Networks Dashboard - Score Badge HTML (Template)

**Location**: Network card HTML template in `networks_dashboard()`

```python
# Before rendering, compute badge HTML
score_badge_html = ""
if net['score'] is not None:
    badge_color_map = {
        'high': '#ef4444',    # red
        'medium': '#eab308',  # yellow
        'low': '#22c55e'      # green
    }
    badge_color = badge_color_map.get(net['score_badge'], '#eab308')
    score_badge_html = f"""<span style="display: inline-block; background-color: {badge_color}; color: #000; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; margin-left: 8px;">{net['score']}</span>"""

# In h3 tag
f"<h3 style=\"margin: 0 0 20px 0; color: var(--text-primary); font-size: 16px;\">{net['name']}{score_badge_html}</h3>"
```

---

## 4. Creator Network Detail - Score Helper Function

**Location**: New function before `@app.route('/creator-network/<network_name>')`

```python
def _build_score_section(score_info: dict) -> str:
    """Build HTML score display section for creator network page"""
    if not score_info or score_info.get('score') is None:
        return ""

    score = score_info['score']
    components = score_info.get('components', {})

    # Determine badge color
    badge_color_map = {
        'high': '#ef4444',    # red
        'medium': '#eab308',  # yellow
        'low': '#22c55e'      # green
    }
    badge_color = badge_color_map.get(score_info.get('score_badge', 'medium'), '#eab308')

    return f"""
            <div class="members-section" style="background: var(--bg-secondary); border-radius: 8px; padding: 20px; border: 1px solid rgba(124, 58, 237, 0.3); margin-bottom: 30px;">
                <h2 style="color: var(--accent-purple); font-size: 16px; margin-bottom: 15px; display: flex; align-items: center; gap: 8px;">
                    📊 Risk Score
                    <span style="display: inline-block; background-color: {badge_color}; color: #000; padding: 4px 8px; border-radius: 4px; font-size: 14px; font-weight: bold; margin-left: auto;">{score} / 100</span>
                </h2>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px;">
                    <div style="background: var(--bg-primary); padding: 12px; border-radius: 6px; border-left: 3px solid rgba(59, 130, 246, 0.5);">
                        <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Connectivity</div>
                        <div style="font-weight: bold; font-size: 18px; color: #3b82f6;">{components.get('connectivity', 0)} / 40</div>
                    </div>
                    <div style="background: var(--bg-primary); padding: 12px; border-radius: 6px; border-left: 3px solid rgba(251, 191, 36, 0.5);">
                        <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Lifecycle</div>
                        <div style="font-weight: bold; font-size: 18px; color: #fbbf24;">{components.get('lifecycle', 0)} / 25</div>
                    </div>
                    <div style="background: var(--bg-primary); padding: 12px; border-radius: 6px; border-left: 3px solid rgba(168, 85, 247, 0.5);">
                        <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Evidence</div>
                        <div style="font-weight: bold; font-size: 18px; color: #a855f7;">{components.get('evidence', 0)} / 35</div>
                    </div>
                </div>
            </div>
    """
```

---

## 5. Creator Network Detail - Score in new_path()

**Location**: `creator_network_page()` → `new_path()` function

```python
def new_path():
    """NEW PATH: Use networks_release and network_membership"""
    network_name_decoded = unquote(network_name)

    # Get network info from networks_release
    network = get_network_release_by_name(network_name_decoded, include_evidence=False)

    if not network:
        return {'error': f"Network '{network_name_decoded}' not found"}, 404

    # Get members from network_membership
    members = get_network_members(network_name_decoded)

    # Get network score
    score_info = get_network_score(network_name_decoded)

    return {
        'network': network,
        'members': members,
        'network_name': network_name_decoded,
        'creator_count': len(members),
        'funder_count': 0,
        'score_info': score_info
    }, 200
```

---

## 6. Creator Network Detail - Score in legacy_path()

**Location**: `creator_network_page()` → `legacy_path()` function (at return statement)

```python
conn.close()

# Get network score
score_info = get_network_score(network_name_decoded)

return {
    'network': network_row,
    'creators_html': creators_html,
    'funders_html': funders_html,
    'creator_count': creator_count,
    'funder_count': funder_count,
    'network_name': network_name_decoded,
    'network_type': network_type,
    'has_cex': has_cex,
    'has_infra': has_infra,
    'score_info': score_info
}, 200
```

---

## 7. Creator Network Detail - Template Integration

**Location**: HTML template in `creator_network_page()`, after header

```python
html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <!-- ... styles ... -->
    </head>
    <body>
        <div class="container">
            <header>
                <h1>🔗 {network_name_decoded}</h1>
                <a href="/networks" class="back-link">← Back to Networks</a>
            </header>

            {_build_score_section(context.get('score_info', {}))}

            <div class="network-members">
                <div class="members-section">
                    <h2>👥 Creators ({context.get('creator_count', 0)})</h2>
                    {context.get('creators_html', '<p style="color: var(--text-secondary);">No creators found</p>')}
                </div>
                <div class="members-section">
                    <h2>💰 Funders ({context.get('funder_count', 0)})</h2>
                    {context.get('funders_html', '<p style="color: var(--text-secondary);">No funders found</p>')}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
```

---

## Summary of Changes

| Section | Function | Change | Lines |
|---------|----------|--------|-------|
| Networks Dashboard | new_path() | Batch score query | +20 |
| Networks Dashboard | new_path() | Score to network object | +5 |
| Networks Dashboard | legacy_path() | Batch score query | +20 |
| Networks Dashboard | legacy_path() | Score to network object | +5 |
| Networks Dashboard | Template | Score badge HTML | +10 |
| Creator Detail | Helper | _build_score_section() | +45 |
| Creator Detail | new_path() | get_network_score() call | +3 |
| Creator Detail | legacy_path() | get_network_score() call | +3 |
| Creator Detail | Template | Score section call | +3 |

**Total**: ~114 lines of new/modified code

---

## Key Design Patterns Used

### Pattern 1: Batched Query (Networks List)
```python
network_names = [n['network_name'] for n in all_networks]
scores_map = {}
# Single query with IN clause
cursor.execute(f'SELECT ... WHERE network_name IN ({placeholders})', network_names)
# O(1) lookup per network
score_info = scores_map.get(network_name)
```

### Pattern 2: Read-Only Helper
```python
def _build_score_section(score_info: dict) -> str:
    # No computation, only HTML rendering
    # Graceful empty return if no data
    if not score_info or score_info.get('score') is None:
        return ""
    # Extract from precomputed JSON
    components = score_info.get('components', {})
```

### Pattern 3: Color Mapping
```python
badge_color_map = {
    'high': '#ef4444',    # red
    'medium': '#eab308',  # yellow
    'low': '#22c55e'      # green
}
badge_color = badge_color_map.get(score_info.get('score_badge'), '#eab308')
```

### Pattern 4: Graceful Degradation
```python
# Networks list: score only shown if it exists
if net['score'] is not None:
    # show badge

# Detail page: score section only shown if it exists
if not score_info or score_info.get('score') is None:
    return ""
```

---

## No Scoring Logic in Templates

✅ **Verified**: All component values come from `components` dict, no computation

```python
# CORRECT - Reading from dict
{components.get('connectivity', 0)} / 40

# NOT USED - No computation like this
{{ network.size * risk_factor }}  # ❌ No math
```

---

## Syntax Validation

```bash
$ python3 -m py_compile main.py
✅ Success (no output = valid)
```

---

End of Phase 4B Code Reference

