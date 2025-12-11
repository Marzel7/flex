import requests
import sqlite3
import json
from datetime import datetime
from typing import Dict, List
from flask import Flask, render_template, jsonify
from threading import Thread
import time

class RaydiumDatabase:
    """Handle SQLite database operations for Raydium pools"""
    
    def __init__(self, db_name: str = "raydium_pools.db"):
        self.db_name = db_name
        self.init_database()
    
    def get_connection(self) -> sqlite3.Connection:
        """Get a new database connection for each operation"""
        return sqlite3.connect(self.db_name, check_same_thread=False)
    
    def init_database(self):
        """Initialize database and create tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amm_id TEXT UNIQUE NOT NULL,
                name TEXT,
                base_mint TEXT,
                quote_mint TEXT,
                liquidity REAL,
                volume_24h REAL,
                price REAL,
                apr REAL,
                first_seen TIMESTAMP,
                last_updated TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pool_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amm_id TEXT NOT NULL,
                liquidity REAL,
                volume_24h REAL,
                price REAL,
                timestamp TIMESTAMP,
                FOREIGN KEY (amm_id) REFERENCES pools(amm_id)
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_amm_id ON pools(amm_id)
        ''')
        
        conn.commit()
        conn.close()
        print(f"Database initialized: {self.db_name}")
    
    def pool_exists(self, amm_id: str) -> bool:
        """Check if pool already exists in database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM pools WHERE amm_id = ?', (amm_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    
    def insert_pool(self, pool: Dict) -> bool:
        """Insert new pool into database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            
            cursor.execute('''
                INSERT INTO pools (
                    amm_id, name, base_mint, quote_mint,
                    liquidity, volume_24h, price, apr,
                    first_seen, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pool.get('ammId'),
                pool.get('name'),
                pool.get('baseMint'),
                pool.get('quoteMint'),
                pool.get('liquidity', 0),
                pool.get('volume24h', 0),
                pool.get('price', 0),
                pool.get('apr', 0),
                timestamp,
                timestamp
            ))
            
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"Error inserting pool: {e}")
            return False
        finally:
            conn.close()
    
    def update_pool(self, pool: Dict):
        """Update existing pool information"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            
            cursor.execute('''
                UPDATE pools SET
                    liquidity = ?,
                    volume_24h = ?,
                    price = ?,
                    apr = ?,
                    last_updated = ?
                WHERE amm_id = ?
            ''', (
                pool.get('liquidity', 0),
                pool.get('volume24h', 0),
                pool.get('price', 0),
                pool.get('apr', 0),
                timestamp,
                pool.get('ammId')
            ))
            
            conn.commit()
        except Exception as e:
            print(f"Error updating pool: {e}")
        finally:
            conn.close()
    
    def add_pool_history(self, pool: Dict):
        """Add pool snapshot to history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            timestamp = datetime.now().isoformat()
            
            cursor.execute('''
                INSERT INTO pool_history (
                    amm_id, liquidity, volume_24h, price, timestamp
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                pool.get('ammId'),
                pool.get('liquidity', 0),
                pool.get('volume24h', 0),
                pool.get('price', 0),
                timestamp
            ))
            
            conn.commit()
        except Exception as e:
            print(f"Error adding pool history: {e}")
        finally:
            conn.close()
    
    def get_pool_count(self) -> int:
        """Get total number of pools in database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM pools')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def get_recent_pools(self, limit: int = 50) -> List[Dict]:
        """Get most recently added pools"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT amm_id, name, liquidity, volume_24h, price, apr, first_seen, last_updated
            FROM pools
            ORDER BY first_seen DESC
            LIMIT ?
        ''', (limit,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'amm_id': row[0],
                'name': row[1],
                'liquidity': row[2],
                'volume_24h': row[3],
                'price': row[4],
                'apr': row[5],
                'first_seen': row[6],
                'last_updated': row[7]
            })
        conn.close()
        return results
    
    def get_top_pools(self, limit: int = 10, order_by: str = 'volume_24h') -> List[Dict]:
        """Get top pools by specified metric"""
        valid_columns = ['liquidity', 'volume_24h', 'apr']
        if order_by not in valid_columns:
            order_by = 'volume_24h'
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = f'''
            SELECT amm_id, name, liquidity, volume_24h, price, apr, first_seen
            FROM pools
            ORDER BY {order_by} DESC
            LIMIT ?
        '''
        
        cursor.execute(query, (limit,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'amm_id': row[0],
                'name': row[1],
                'liquidity': row[2],
                'volume_24h': row[3],
                'price': row[4],
                'apr': row[5],
                'first_seen': row[6]
            })
        conn.close()
        return results


class RaydiumMonitor:
    """Monitor Raydium DEX tokens and liquidity pools"""
    
    def __init__(self, db_name: str = "raydium_pools.db"):
        self.raydium_api = "https://api.raydium.io/v2"
        self.session = requests.Session()
        self.db = RaydiumDatabase(db_name)
        self.latest_pools = []
        self.is_running = False
        
    def get_all_pools(self) -> List[Dict]:
        """Fetch all Raydium liquidity pools"""
        try:
            response = self.session.get(f"{self.raydium_api}/main/pairs")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching pools: {e}")
            return []
    
    def process_pools(self, pools: List[Dict], min_liquidity: float = 1000) -> List[Dict]:
        """Process pools and store new ones in database"""
        new_pools = []
        
        for pool in pools:
            amm_id = pool.get('ammId')
            if not amm_id:
                continue
            
            if not self.db.pool_exists(amm_id):
                if pool.get('liquidity', 0) >= min_liquidity:
                    if self.db.insert_pool(pool):
                        new_pools.append(pool)
                        self.db.add_pool_history(pool)
            else:
                self.db.update_pool(pool)
                self.db.add_pool_history(pool)
        
        return new_pools
    
    def monitor_loop(self, interval: int = 60, min_liquidity: float = 1000):
        """Background monitoring loop"""
        self.is_running = True
        print(f"Monitor started: checking every {interval}s, min liquidity ${min_liquidity:,.0f}")
        
        while self.is_running:
            try:
                pools = self.get_all_pools()
                if pools:
                    new_pools = self.process_pools(pools, min_liquidity)
                    if new_pools:
                        self.latest_pools = new_pools
                        print(f"Found {len(new_pools)} new pool(s)")
                
                time.sleep(interval)
            except Exception as e:
                print(f"Error in monitor loop: {e}")
                time.sleep(interval)
    
    def start_background_monitor(self, interval: int = 60, min_liquidity: float = 1000):
        """Start monitoring in background thread"""
        thread = Thread(target=self.monitor_loop, args=(interval, min_liquidity), daemon=True)
        thread.start()
        return thread


# Flask Web Application
app = Flask(__name__)
monitor = RaydiumMonitor()

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Raydium Token Monitor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .header {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 30px;
        }
        
        h1 {
            color: #667eea;
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #666;
            font-size: 1.1em;
        }
        
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .stat-label {
            color: #888;
            font-size: 0.9em;
            margin-bottom: 5px;
        }
        
        .stat-value {
            color: #333;
            font-size: 2em;
            font-weight: bold;
        }
        
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        
        .tab {
            background: white;
            border: none;
            padding: 15px 30px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .tab.active {
            background: #667eea;
            color: white;
        }
        
        .tab:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.15);
        }
        
        .pool-grid {
            display: grid;
            gap: 20px;
        }
        
        .pool-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            animation: slideIn 0.5s ease-out;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .pool-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
        }
        
        .pool-name {
            font-size: 1.5em;
            font-weight: bold;
            color: #333;
        }
        
        .new-badge {
            background: #4CAF50;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: bold;
        }
        
        .pool-details {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .detail-item {
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
        }
        
        .detail-label {
            color: #888;
            font-size: 0.85em;
            margin-bottom: 5px;
        }
        
        .detail-value {
            color: #333;
            font-size: 1.2em;
            font-weight: bold;
        }
        
        .pool-id {
            color: #888;
            font-size: 0.85em;
            margin-top: 15px;
            font-family: monospace;
        }
        
        .loading {
            text-align: center;
            color: white;
            font-size: 1.5em;
            padding: 50px;
        }
        
        .refresh-info {
            color: white;
            text-align: center;
            margin-top: 20px;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Raydium Token Monitor</h1>
            <p class="subtitle">Real-time tracking of new Raydium liquidity pools</p>
        </div>
        
        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="stat-label">Total Pools</div>
                <div class="stat-value" id="totalPools">-</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">New Today</div>
                <div class="stat-value" id="newToday">-</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Liquidity</div>
                <div class="stat-value" id="totalLiquidity">-</div>
            </div>
        </div>
        
        <div class="tabs">
            <button class="tab active" onclick="switchTab('recent')">Recent Pools</button>
            <button class="tab" onclick="switchTab('volume')">Top by Volume</button>
            <button class="tab" onclick="switchTab('liquidity')">Top by Liquidity</button>
        </div>
        
        <div id="poolsContainer" class="pool-grid">
            <div class="loading">Loading pools...</div>
        </div>
        
        <div class="refresh-info">Auto-refreshing every 30 seconds</div>
    </div>
    
    <script>
        let currentTab = 'recent';
        
        function formatNumber(num) {
            if (num >= 1000000) {
                return '$' + (num / 1000000).toFixed(2) + 'M';
            } else if (num >= 1000) {
                return '$' + (num / 1000).toFixed(2) + 'K';
            }
            return '$' + num.toFixed(2);
        }
        
        function formatPrice(price) {
            if (price < 0.01) {
                return '$' + price.toFixed(8);
            }
            return '$' + price.toFixed(4);
        }
        
        function isNewPool(firstSeen) {
            const poolTime = new Date(firstSeen);
            const now = new Date();
            const hoursDiff = (now - poolTime) / (1000 * 60 * 60);
            return hoursDiff < 24;
        }
        
        function renderPool(pool) {
            const isNew = isNewPool(pool.first_seen);
            return `
                <div class="pool-card">
                    <div class="pool-header">
                        <div class="pool-name">${pool.name}</div>
                        ${isNew ? '<span class="new-badge">NEW</span>' : ''}
                    </div>
                    <div class="pool-details">
                        <div class="detail-item">
                            <div class="detail-label">Liquidity</div>
                            <div class="detail-value">${formatNumber(pool.liquidity)}</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">24h Volume</div>
                            <div class="detail-value">${formatNumber(pool.volume_24h)}</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">Price</div>
                            <div class="detail-value">${formatPrice(pool.price)}</div>
                        </div>
                        <div class="detail-item">
                            <div class="detail-label">APR</div>
                            <div class="detail-value">${pool.apr.toFixed(2)}%</div>
                        </div>
                    </div>
                    <div class="pool-id">Pool ID: ${pool.amm_id}</div>
                </div>
            `;
        }
        
        function updateStats(data) {
            document.getElementById('totalPools').textContent = data.total_pools.toLocaleString();
            const newToday = data.pools.filter(p => isNewPool(p.first_seen)).length;
            document.getElementById('newToday').textContent = newToday;
            const totalLiq = data.pools.reduce((sum, p) => sum + p.liquidity, 0);
            document.getElementById('totalLiquidity').textContent = formatNumber(totalLiq);
        }
        
        function updatePools() {
            fetch(`/api/pools/${currentTab}`)
                .then(response => response.json())
                .then(data => {
                    updateStats(data);
                    const container = document.getElementById('poolsContainer');
                    if (data.pools.length === 0) {
                        container.innerHTML = '<div class="loading">No pools found yet. Monitoring...</div>';
                    } else {
                        container.innerHTML = data.pools.map(renderPool).join('');
                    }
                })
                .catch(error => {
                    console.error('Error fetching pools:', error);
                });
        }
        
        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            updatePools();
        }
        
        // Initial load
        updatePools();
        
        // Auto-refresh every 30 seconds
        setInterval(updatePools, 30000);
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return HTML_TEMPLATE

@app.route('/api/pools/<tab_type>')
def get_pools(tab_type):
    """API endpoint to get pools based on tab selection"""
    try:
        if tab_type == 'recent':
            pools = monitor.db.get_recent_pools(50)
        elif tab_type == 'volume':
            pools = monitor.db.get_top_pools(50, 'volume_24h')
        elif tab_type == 'liquidity':
            pools = monitor.db.get_top_pools(50, 'liquidity')
        else:
            pools = monitor.db.get_recent_pools(50)
        
        return jsonify({
            'pools': pools,
            'total_pools': monitor.db.get_pool_count()
        })
    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({'pools': [], 'total_pools': 0})

def main():
    """Main function to run the monitor with web UI"""
    print("=" * 60)
    print("Raydium Token Monitor - Web UI")
    print("=" * 60)
    
    # Configuration
    CHECK_INTERVAL = 60  # seconds
    MIN_LIQUIDITY = 5000  # USD
    PORT = 5001  # Changed from 5000 to avoid conflicts
    
    # Start background monitoring
    monitor.start_background_monitor(interval=CHECK_INTERVAL, min_liquidity=MIN_LIQUIDITY)
    
    # Start web server
    print("\n🌐 Starting web interface...")
    print(f"📊 Open your browser to: http://localhost:{PORT}")
    print("\nPress Ctrl+C to stop\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == "__main__":
    main()
    