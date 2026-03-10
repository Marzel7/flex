#!/usr/bin/env python3
"""
Flex Application Entry Point

This script sets up the environment and runs the main application.
It adjusts sys.path to allow absolute imports of the reorganized package structure.
"""
import sys
import os
from pathlib import Path

# Add project root to path so we can import src modules
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set database path
db_path = os.path.join(PROJECT_ROOT, 'database', 'flex_complete_database.db')
os.environ.setdefault('DB_PATH', db_path)
os.environ.setdefault('RPC_METRICS_DB', db_path)

# Import and run main app
if __name__ == '__main__':
    from src.core.main import app
    
    # Create database directory if needed
    db_dir = PROJECT_ROOT / 'database'
    db_dir.mkdir(exist_ok=True)
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5002, debug=False)
