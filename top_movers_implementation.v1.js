/**
 * TOP MOVERS PANEL - Real-time leaderboard powered by SSE price stream
 *
 * Features:
 * - Top Gainers (highest % change in rolling window)
 * - Top Losers (lowest % change in rolling window)
 * - Most Active (most updates in rolling window)
 * - 5-minute rolling window (configurable)
 * - Debounced render (1s interval)
 * - No backend changes required
 * - Smooth UI updates, no flicker
 */

// =====================================================================
// TOP MOVERS STATE MANAGEMENT
// =====================================================================

const TOP_MOVERS = {
  // Configuration
  config: {
    windowMs: 5 * 60 * 1000,      // 5-minute rolling window
    renderIntervalMs: 1000,         // Debounce render to 1 second
    maxTokensPerCategory: 10,       // Top 10 in each category
    minUpdatesForRanking: 2,        // Need at least 2 updates to calculate %change
  },

  // State: mint → {
  //   mint,
  //   currentPrice,
  //   previousPrice,
  //   source,
  //   lastUpdatedAt,
  //   updates: [{price, time}, ...]
  // }
  tokenMap: new Map(),

  // Render state
  lastRendered: 0,
  renderScheduled: false,
  needsRender: false,

  // =====================================================================
  // PRICE UPDATE HANDLER
  // =====================================================================

  onPriceUpdate(update) {
    const { mint, price_usd, source, updated_at } = update;

    let token = this.tokenMap.get(mint);
    if (!token) {
      token = {
        mint,
        updates: [],
        currentPrice: price_usd,
        previousPrice: price_usd,
        source,
        lastUpdatedAt: Date.now(),
      };
      this.tokenMap.set(mint, token);
    }

    // Store previous price before updating
    token.previousPrice = token.currentPrice || price_usd;
    token.currentPrice = price_usd;
    token.source = source;
    token.lastUpdatedAt = Date.now();

    // Record update in history
    token.updates.push({
      price: price_usd,
      time: Date.now(),
    });

    // Prune old history
    this.pruneHistory(token);

    // Schedule re-render (batched)
    this.scheduleRender();
  },

  // =====================================================================
  // HISTORY MANAGEMENT
  // =====================================================================

  pruneHistory(token) {
    const cutoff = Date.now() - this.config.windowMs;
    token.updates = token.updates.filter(u => u.time > cutoff);
  },

  // =====================================================================
  // CALCULATIONS
  // =====================================================================

  getPercentChange(token) {
    if (token.updates.length < this.config.minUpdatesForRanking) {
      return 0;
    }

    const first = token.updates[0].price;
    const last = token.currentPrice;

    if (first === 0) return 0;
    return ((last - first) / first) * 100;
  },

  getUpdateCount(token) {
    return token.updates.length;
  },

  // =====================================================================
  // RANKING
  // =====================================================================

  getTopMovers() {
    const tokens = Array.from(this.tokenMap.values());

    // Filter: need at least minUpdates
    const validTokens = tokens.filter(
      t => this.getUpdateCount(t) >= this.config.minUpdatesForRanking
    );

    // Top Gainers (highest % change)
    const gainers = validTokens
      .sort((a, b) => this.getPercentChange(b) - this.getPercentChange(a))
      .slice(0, this.config.maxTokensPerCategory);

    // Top Losers (lowest % change)
    const losers = validTokens
      .sort((a, b) => this.getPercentChange(a) - this.getPercentChange(b))
      .slice(0, this.config.maxTokensPerCategory);

    // Most Active (most updates)
    const active = tokens
      .sort((a, b) => this.getUpdateCount(b) - this.getUpdateCount(a))
      .slice(0, this.config.maxTokensPerCategory);

    return { gainers, losers, active };
  },

  // =====================================================================
  // RENDER SCHEDULING (DEBOUNCED)
  // =====================================================================

  scheduleRender() {
    this.needsRender = true;

    if (this.renderScheduled) return;
    this.renderScheduled = true;

    const timeSinceLastRender = Date.now() - this.lastRendered;
    const delayMs = Math.max(0, this.config.renderIntervalMs - timeSinceLastRender);

    setTimeout(() => {
      this.renderScheduled = false;
      if (this.needsRender) {
        this.render();
      }
    }, delayMs);
  },

  // =====================================================================
  // RENDERING
  // =====================================================================

  render() {
    this.needsRender = false;
    this.lastRendered = Date.now();

    const container = document.getElementById('top-movers-container');
    if (!container) return;

    const { gainers, losers, active } = this.getTopMovers();

    container.innerHTML = `
      <div class="top-movers-panel">
        ${this.renderCategory('Top Gainers', gainers, 'gainers')}
        ${this.renderCategory('Top Losers', losers, 'losers')}
        ${this.renderCategory('Most Active', active, 'active')}
      </div>
    `;
  },

  renderCategory(title, tokens, type) {
    if (tokens.length === 0) {
      return `
        <div class="movers-card">
          <div class="movers-header">
            <h6>${title}</h6>
          </div>
          <div class="movers-body">
            <p class="text-muted">Waiting for data...</p>
          </div>
        </div>
      `;
    }

    const rows = tokens.map((token, idx) => {
      const change = type === 'active'
        ? token.updates.length
        : this.getPercentChange(token);

      const changeColor = type === 'active'
        ? '#6b7280'  // neutral gray
        : change >= 0
          ? '#22c55e'  // green for gainers
          : '#ef4444'; // red for losers

      const changeText = type === 'active'
        ? `${token.updates.length} updates`
        : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;

      const sourceIcon = token.source === 'pool' ? '🌊' : '📊';
      const sourceBg = token.source === 'pool' ? 'bg-success' : 'bg-secondary';

      return `
        <div class="movers-row" data-mint="${token.mint}">
          <div class="movers-rank">${idx + 1}</div>
          <div class="movers-mint">${token.mint.substring(0, 8)}...</div>
          <div class="movers-price">$${token.currentPrice.toExponential(2)}</div>
          <div class="movers-change" style="color: ${changeColor};">
            ${changeText}
          </div>
          <div class="movers-source">
            <span class="badge ${sourceBg} movers-badge">${sourceIcon}</span>
          </div>
        </div>
      `;
    }).join('');

    return `
      <div class="movers-card">
        <div class="movers-header">
          <h6>${title}</h6>
          <small>${tokens.length} tokens</small>
        </div>
        <div class="movers-body">
          ${rows}
        </div>
      </div>
    `;
  },

  // =====================================================================
  // INITIALIZATION
  // =====================================================================

  init(priceUpdateHandler) {
    // Hook into price stream (typically called from updateTokenPrice)
    console.log('[TOP_MOVERS] ✅ Initialized');

    // Store reference to price handler so we can call it
    this.priceUpdateHandler = priceUpdateHandler;
  },
};

// =====================================================================
// INTEGRATION WITH EXISTING PRICE STREAM
// =====================================================================

/**
 * Call this from the SSE message handler (in initPriceStream)
 * before or after calling updateTokenPrice()
 */
function handlePriceUpdateForMovers(update) {
  TOP_MOVERS.onPriceUpdate(update);
}

/**
 * Inject into existing updateTokenPrice function
 * Example:
 *   es.onmessage = (event) => {
 *     const update = JSON.parse(event.data);
 *     if (update.type === 'price_update') {
 *       updateTokenPrice(update);       // existing function
 *       handlePriceUpdateForMovers(update); // NEW: add this line
 *     }
 *   };
 */

/**
 * Create container in HTML (add to dashboard)
 * <div id="top-movers-container"></div>
 */
