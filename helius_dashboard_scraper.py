#!/usr/bin/env python3
"""
Helius Dashboard Scraper

Scrape real account usage from Helius dashboard via web browser automation.
This retrieves actual credits used/remaining directly from your Helius account.

Requires: pip install selenium webdriver-manager

Usage:
    python helius_dashboard_scraper.py [email] [password]
    python helius_dashboard_scraper.py --auto (uses env vars)
    python helius_dashboard_scraper.py --headless (runs without window)
"""

import os
import sys
import json
from datetime import datetime
from typing import Optional, Dict
import sqlite3

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")

# Helius credentials (from environment or args)
HELIUS_EMAIL = os.getenv("HELIUS_EMAIL", "")
HELIUS_PASSWORD = os.getenv("HELIUS_PASSWORD", "")


def _connect():
    """Create connection with optimal PRAGMA settings"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_history_table():
    """Create helius_account_history table for tracking"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS helius_account_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          credits_used INTEGER,
          credits_remaining INTEGER,
          monthly_budget INTEGER,
          api_calls INTEGER,
          estimated_daily_burn INTEGER,
          recorded_at TIMESTAMP
        )
        """)
        conn.commit()
    finally:
        conn.close()


def scrape_helius_dashboard(
    email: str, password: str, headless: bool = True
) -> Optional[Dict]:
    """
    Scrape actual account usage from Helius dashboard

    Returns:
    {
        "credits_used": int,
        "credits_remaining": int,
        "monthly_budget": int,
        "api_calls": int,
        "timestamp": ISO datetime string,
        "source": "dashboard"
    }

    Or None if scraping fails
    """
    if not HAS_SELENIUM:
        print(
            "[HELIUS] ❌ Selenium not installed. Install with: pip install selenium webdriver-manager",
            flush=True,
        )
        return None

    if not email or not password:
        print("[HELIUS] ❌ Email and password required", flush=True)
        return None

    try:
        # Setup Chrome options
        chrome_options = Options()
        if headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # Start driver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        print("[HELIUS] 🔐 Logging into dashboard via Google...", flush=True)

        # Navigate to login
        driver.get("https://dashboard.helius.dev/login")
        import time

        # Wait for login buttons
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "button"))
        )
        time.sleep(1)

        # Step 1: Click Google button
        print("[HELIUS] 🔵 Clicking Google login button...", flush=True)
        buttons = driver.find_elements(By.TAG_NAME, "button")
        google_button = None
        for btn in buttons:
            if "Google" in btn.text:
                google_button = btn
                break

        if not google_button:
            raise Exception("Could not find Google login button")

        google_button.click()
        time.sleep(2)

        # Step 2: Wait for Google OAuth or URL change
        print("[HELIUS] 🔄 Waiting for Google authentication...", flush=True)
        time.sleep(3)

        # Check if we have multiple windows (popup) or if URL changed (redirect)
        current_url = driver.current_url
        print(f"[HELIUS] Current URL: {current_url}", flush=True)

        # Try to handle popup window if it exists
        if len(driver.window_handles) > 1:
            print("[HELIUS] 📱 OAuth popup detected, switching...", flush=True)
            driver.switch_to.window(driver.window_handles[1])
            time.sleep(2)
        elif "accounts.google.com" in driver.current_url or "google.com" in driver.current_url:
            print("[HELIUS] 📱 OAuth redirected, on Google login...", flush=True)
        else:
            print("[HELIUS] ℹ️ OAuth may be in iframe or same window", flush=True)

        # Step 3: Try to enter Gmail email (works for popup or redirect)
        print("[HELIUS] 📧 Entering Gmail address...", flush=True)
        try:
            # Try multiple selectors for email field
            email_field = None
            try:
                email_field = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "identifierId"))
                )
            except:
                try:
                    email_field = driver.find_element(By.NAME, "identifier")
                except:
                    email_field = driver.find_element(By.CSS_SELECTOR, "input[type='email']")

            email_field.send_keys(email)
            time.sleep(1)

            # Click Next
            try:
                next_button = driver.find_element(By.ID, "identifierNext")
            except:
                next_button = driver.find_element(By.XPATH, "//button[@jsname='LgbsSe']")
            next_button.click()
            time.sleep(2)
        except Exception as e:
            raise Exception(f"Could not enter Gmail email: {str(e)}")

        # Step 4: Enter Gmail password
        print("[HELIUS] 🔑 Entering Gmail password...", flush=True)
        try:
            password_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "password"))
            )
            password_field.send_keys(password)
            time.sleep(1)

            # Click Next
            try:
                next_button = driver.find_element(By.ID, "passwordNext")
            except:
                next_button = driver.find_element(By.XPATH, "//button[@jsname='LgbsSe']")
            next_button.click()
            time.sleep(3)
        except Exception as e:
            raise Exception(f"Could not enter Gmail password: {str(e)}")

        # Step 5: Switch back to main window if needed
        print("[HELIUS] ↩️  Returning to dashboard...", flush=True)
        try:
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[0])
        except:
            pass
        time.sleep(3)

        # Wait for dashboard to load
        print("[HELIUS] ⏳ Waiting for dashboard...", flush=True)
        time.sleep(5)

        # Extract credits info
        page_source = driver.page_source
        page_text = driver.find_element(By.TAG_NAME, "body").text

        # Check if still on login page (failed auth)
        if "Email" in page_text and "Password" in page_text and "Sign In" in page_text:
            print("[HELIUS] ❌ Still on login page - credentials may be incorrect or 2FA enabled", flush=True)
            print(f"[HELIUS] 📋 Page contains: {page_text[:500]}", flush=True)
            return None

        # Parse usage values from page
        usage = parse_dashboard_page(page_source)

        if usage:
            usage["source"] = "dashboard"
            usage["timestamp"] = datetime.now().isoformat()
            print("[HELIUS] ✅ Successfully scraped dashboard", flush=True)
        else:
            print("[HELIUS] ⚠️ Could not parse dashboard data", flush=True)
            print(f"[HELIUS] 📄 Page preview: {page_text[:300]}", flush=True)

        driver.quit()
        return usage

    except Exception as e:
        print(f"[HELIUS] ❌ Scraping failed: {str(e)[:200]}", flush=True)
        try:
            driver.quit()
        except:
            pass
        return None


def parse_dashboard_page(html: str) -> Optional[Dict]:
    """
    Parse usage data from Helius dashboard HTML

    Looks for patterns like:
    - "Credits Used: 24,682"
    - "Credits Remaining: 975,318"
    - "Monthly Budget: 1,000,000"
    """
    try:
        import re

        # Pattern: "Credits Used: X"
        credits_used_match = re.search(r"Credits\s+Used[:\s]+([0-9,]+)", html, re.IGNORECASE)
        credits_remaining_match = re.search(
            r"Credits\s+Remaining[:\s]+([0-9,]+)", html, re.IGNORECASE
        )
        budget_match = re.search(r"Monthly\s+Budget[:\s]+([0-9,]+)", html, re.IGNORECASE)
        api_calls_match = re.search(r"API\s+Calls[:\s]+([0-9,]+)", html, re.IGNORECASE)

        if not all([credits_used_match, credits_remaining_match, budget_match]):
            return None

        usage = {
            "credits_used": int(credits_used_match.group(1).replace(",", "")),
            "credits_remaining": int(credits_remaining_match.group(1).replace(",", "")),
            "monthly_budget": int(budget_match.group(1).replace(",", "")),
            "api_calls": (
                int(api_calls_match.group(1).replace(",", ""))
                if api_calls_match
                else 0
            ),
        }

        return usage
    except Exception as e:
        print(f"[HELIUS] ⚠️ Parse error: {str(e)[:100]}", flush=True)
        return None


def record_account_usage(usage: Dict):
    """Store account usage in database history"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        INSERT INTO helius_account_history(
          credits_used,
          credits_remaining,
          monthly_budget,
          api_calls,
          estimated_daily_burn,
          recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                usage.get("credits_used", 0),
                usage.get("credits_remaining", 0),
                usage.get("monthly_budget", 0),
                usage.get("api_calls", 0),
                usage.get("estimated_daily_burn", 0),
                usage.get("timestamp"),
            ),
        )
        conn.commit()
        print("[HELIUS] 💾 Recorded usage in database", flush=True)
    finally:
        conn.close()


def print_usage(usage: Dict):
    """Pretty print usage data"""
    print("\n" + "=" * 80, flush=True)
    print("[HELIUS] 📊 ACCOUNT USAGE (from Dashboard)", flush=True)
    print("=" * 80, flush=True)
    print(f"\nCredits Used:       {usage.get('credits_used', 0):>12,}", flush=True)
    print(f"Credits Remaining:  {usage.get('credits_remaining', 0):>12,}", flush=True)
    print(f"Monthly Budget:     {usage.get('monthly_budget', 0):>12,}", flush=True)
    print(f"API Calls:          {usage.get('api_calls', 0):>12,}", flush=True)

    used = usage.get("credits_used", 0)
    budget = usage.get("monthly_budget", 0)
    if budget > 0:
        percent = (used / budget) * 100
        print(f"Usage:              {percent:>12.1f}%", flush=True)

    print(f"Source:             {usage.get('source', 'unknown'):>12}", flush=True)
    print(f"Timestamp:          {usage.get('timestamp', 'unknown')}", flush=True)
    print("=" * 80 + "\n", flush=True)


def update_config_with_usage(usage: Dict):
    """Update rpc_metrics_config.py with actual usage"""
    try:
        from rpc_metrics_config import PlanConfig

        PlanConfig.CURRENT_USAGE["credits_used_today"] = usage.get("credits_used", 0)
        PlanConfig.CURRENT_USAGE["credits_remaining"] = usage.get("credits_remaining", 0)

        print("[HELIUS] ✅ Updated rpc_metrics_config.py", flush=True)
    except Exception as e:
        print(f"[HELIUS] ⚠️ Could not update config: {str(e)[:100]}", flush=True)


if __name__ == "__main__":
    if not HAS_SELENIUM:
        print(
            "[HELIUS] ❌ Selenium not installed. Install with:\n  pip install selenium webdriver-manager",
            flush=True,
        )
        sys.exit(1)

    # Parse arguments
    email = None
    password = None
    headless = True

    if "--headless" in sys.argv:
        headless = True
    if "--no-headless" in sys.argv or "--show" in sys.argv:
        headless = False

    if "--auto" in sys.argv or len(sys.argv) < 2:
        email = HELIUS_EMAIL
        password = HELIUS_PASSWORD
    elif len(sys.argv) >= 3:
        email = sys.argv[1]
        password = sys.argv[2]

    if not email or not password:
        print(
            "[HELIUS] Usage:\n"
            "  python helius_dashboard_scraper.py [email] [password]\n"
            "  python helius_dashboard_scraper.py --auto (uses HELIUS_EMAIL and HELIUS_PASSWORD env vars)\n"
            "  python helius_dashboard_scraper.py --show (show browser window)\n",
            flush=True,
        )
        sys.exit(1)

    # Initialize
    ensure_history_table()

    # Scrape
    print("[HELIUS] 🌐 Scraping Helius dashboard...", flush=True)
    usage = scrape_helius_dashboard(email, password, headless=headless)

    if usage:
        print_usage(usage)
        record_account_usage(usage)
        update_config_with_usage(usage)
        print("[HELIUS] ✅ Done", flush=True)
    else:
        print("[HELIUS] ❌ Failed to retrieve usage", flush=True)
        sys.exit(1)
