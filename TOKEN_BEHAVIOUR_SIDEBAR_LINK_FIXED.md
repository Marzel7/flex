# Token Behaviour Sidebar Link — Now Visible ✅

**Status:** Link is now visible in the sidebar and fully functional

---

## Where to Find It

**On the main dashboard page (`http://localhost:5002/`):**

Left sidebar → **Dashboard Pages** section → **Token Behaviour** (📊 chart icon)

### Sidebar Order
```
🏠 Dashboard
📊 Launch Radar
🏢 Organization Explorer
📋 Organization Detail
📈 Launch Waves
🔬 Dev Clusters
🔍 Signal Explorer
🧠 Early Predictions
📊 Token Behaviour  ← HERE
👛 Wallet Intelligence
👤 Dev Fingerprint
```

---

## What Was Fixed

The home page was using a hardcoded HTML template instead of the `flex_dashboard.html` file. Changed:

**Before:**
```python
@app.route('/')
def index():
    return HTML_TEMPLATE  # Hardcoded, outdated
```

**After:**
```python
@app.route('/')
def index():
    return render_template('flex_dashboard.html', page='dashboard')
```

Now the home page uses the same template as all other dashboard pages, including the Token Behaviour leaderboard link.

---

## Quick Start

1. **Open dashboard:** `http://localhost:5002/`

2. **Click the sidebar link:** 📊 **Token Behaviour**

3. **Start the monitor** (if not already running):
   ```bash
   python3 src/core/token_behaviour_monitor.py
   ```

4. **Wait 5+ minutes** for first tokens to be classified

5. **Refresh the page** to see tokens appear in the leaderboard

---

## What You'll See

**Token Behaviour Leaderboard page shows:**
- Summary stats (total classified, breakdown by category)
- 5 color-coded leaderboard tables:
  - 💥 Immediate Rug
  - 🚀 Runner
  - 📈 Choppy Runner
  - 📉 Rug Pull
  - ⬇️ Slow Rug
- Top 10 tokens per category with confidence, returns, drawdown, snapshots, lifetime

---

## Link is Now Live

Fully integrated into the dashboard sidebar. Click anytime to view the token behaviour leaderboard!
