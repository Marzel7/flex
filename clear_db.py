#!/usr/bin/env python3
"""Clear all pool entries from the database while preserving the schema."""

import sqlite3

def clear_pools():
    conn = sqlite3.connect('raydium_pools.db')
    cursor = conn.cursor()

    cursor.execute('DELETE FROM pools')
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    print(f"Deleted {deleted} pool entries from database")

if __name__ == '__main__':
    clear_pools()
