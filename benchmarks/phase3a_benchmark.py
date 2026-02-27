#!/usr/bin/env python3
"""
Phase 3A Benchmark Script

Measures performance of Phase 2C endpoints (new path vs legacy path).
Benchmarks 4 endpoints with configurable iterations.

Usage:
    python3 phase3a_benchmark.py [--url BASE_URL] [--iterations N]

Examples:
    python3 phase3a_benchmark.py
    python3 phase3a_benchmark.py --url http://localhost:5002 --iterations 30
"""

import sys
import time
import sqlite3
import argparse
import statistics
from urllib.parse import quote
from datetime import datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error


def get_network_data(db_path="pumpswap_tokens.db"):
    """
    Select a representative network for benchmarking.
    Prefers networks_release if available, falls back to creator_networks.

    Returns:
        tuple: (network_name, network_id) or (None, None) if no networks found
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try networks_release first (new path)
        try:
            cursor.execute("""
                SELECT network_name FROM networks_release
                ORDER BY network_size DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                network_name = row['network_name']
                conn.close()
                # For network_id, use 1 (deterministic for Phase 2C mapping)
                return network_name, 1
        except sqlite3.OperationalError:
            pass

        # Fallback to creator_networks
        cursor.execute("""
            SELECT network_name FROM creator_networks
            WHERE network_name IS NOT NULL LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            network_name = row['network_name']
            conn.close()
            return network_name, 1

        conn.close()
        return None, None

    except Exception as e:
        print(f"Warning: Could not select network data: {e}", file=sys.stderr)
        return "test_network", 1


def make_request(method, url, session=None):
    """
    Make HTTP request and return status_code and elapsed_ms.

    Args:
        method: "GET"
        url: Full URL to request
        session: requests.Session object (if using requests library)

    Returns:
        tuple: (status_code, elapsed_ms) or (None, None) if error
    """
    try:
        start = time.time()

        if HAS_REQUESTS and session:
            response = session.get(url, timeout=30)
            elapsed_ms = (time.time() - start) * 1000
            return response.status_code, elapsed_ms
        else:
            # urllib fallback
            request = urllib.request.Request(url)
            try:
                response = urllib.request.urlopen(request, timeout=30)
                elapsed_ms = (time.time() - start) * 1000
                return response.status, elapsed_ms
            except urllib.error.HTTPError as e:
                elapsed_ms = (time.time() - start) * 1000
                return e.code, elapsed_ms

    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return None, None


def benchmark_endpoint(base_url, endpoint_path, mode, iterations=20):
    """
    Benchmark a single endpoint in a given mode.

    Args:
        base_url: e.g. "http://127.0.0.1:5002"
        endpoint_path: e.g. "/networks" or "/api/funding-networks"
        mode: "new" or "legacy"
        iterations: number of requests

    Returns:
        dict: {cold, warm_avg, p95, timings}
    """
    import os

    # Set force mode environment variable
    old_mode = os.environ.get('PHASE2C_FORCE_MODE')
    os.environ['PHASE2C_FORCE_MODE'] = mode

    url = f"{base_url}{endpoint_path}".rstrip('/')
    timings = []

    # Create session for requests library
    session = None
    if HAS_REQUESTS:
        session = requests.Session()

    print(f"  Benchmarking {endpoint_path} ({mode} path, {iterations} iterations)...")

    for i in range(iterations):
        status_code, elapsed_ms = make_request("GET", url, session)

        if status_code is None:
            print(f"    Iteration {i+1}: FAILED", file=sys.stderr)
            if session:
                session.close()
            if old_mode:
                os.environ['PHASE2C_FORCE_MODE'] = old_mode
            else:
                os.environ.pop('PHASE2C_FORCE_MODE', None)
            return None

        if status_code != 200:
            print(f"    Iteration {i+1}: Status {status_code}", file=sys.stderr)
            if session:
                session.close()
            if old_mode:
                os.environ['PHASE2C_FORCE_MODE'] = old_mode
            else:
                os.environ.pop('PHASE2C_FORCE_MODE', None)
            return None

        timings.append(elapsed_ms)
        if (i + 1) % 5 == 0:
            print(f"    Completed {i+1}/{iterations} requests", file=sys.stderr)

    if session:
        session.close()

    # Restore original mode
    if old_mode:
        os.environ['PHASE2C_FORCE_MODE'] = old_mode
    else:
        os.environ.pop('PHASE2C_FORCE_MODE', None)

    # Calculate stats
    cold = timings[0]
    warm_avg = statistics.mean(timings[1:]) if len(timings) > 1 else 0
    p95 = None
    if len(timings) >= 2:
        try:
            p95 = statistics.quantiles(timings, n=20)[18]  # 95th percentile
        except:
            p95 = sorted(timings)[int(len(timings) * 0.95)]

    return {
        'cold': cold,
        'warm_avg': warm_avg,
        'p95': p95,
        'timings': timings
    }


def generate_report(results, network_name, network_id, iterations, output_file=None):
    """
    Generate and print benchmark report.

    Args:
        results: dict of benchmark results by endpoint and mode
        network_name: network used for testing
        network_id: network ID used for testing
        iterations: number of iterations
        output_file: optional file path to write report
    """
    report_lines = []

    report_lines.append("=" * 80)
    report_lines.append("PHASE 3A BENCHMARK REPORT")
    report_lines.append("=" * 80)
    report_lines.append(f"Timestamp: {datetime.now().isoformat()}")
    report_lines.append(f"Iterations per endpoint/mode: {iterations}")
    report_lines.append(f"Test network: {network_name} (id={network_id})")
    report_lines.append("")

    endpoints = [
        ('/api/funding-networks', 'API'),
        ('/api/funding-network-details/1', 'API'),
        ('/networks', 'HTML'),
        (f'/creator-network/{quote(network_name, safe="")}', 'HTML')
    ]

    for endpoint_path, endpoint_type in endpoints:
        report_lines.append("-" * 80)
        report_lines.append(f"Endpoint: {endpoint_path} ({endpoint_type})")
        report_lines.append("-" * 80)

        for mode in ['new', 'legacy']:
            key = (endpoint_path, mode)
            if key not in results:
                report_lines.append(f"  {mode.upper()}: FAILED")
                continue

            result = results[key]
            report_lines.append(f"  {mode.upper()} PATH:")
            report_lines.append(f"    Cold start:     {result['cold']:.2f} ms")
            report_lines.append(f"    Warm average:   {result['warm_avg']:.2f} ms")
            if result['p95'] is not None:
                report_lines.append(f"    P95 latency:    {result['p95']:.2f} ms")
            report_lines.append("")

        # Calculate speedup
        new_key = (endpoint_path, 'new')
        legacy_key = (endpoint_path, 'legacy')
        if new_key in results and legacy_key in results:
            new_avg = results[new_key]['warm_avg']
            legacy_avg = results[legacy_key]['warm_avg']
            if legacy_avg > 0:
                speedup = (legacy_avg - new_avg) / legacy_avg * 100
                direction = "faster" if speedup > 0 else "slower"
                report_lines.append(f"  Speedup: {abs(speedup):.1f}% {direction} (new path)")
            report_lines.append("")

    report_lines.append("=" * 80)
    report_lines.append("Summary:")
    report_lines.append("  - 'Cold start' is the first request (may include app warmup)")
    report_lines.append("  - 'Warm average' is the mean of requests 2..N (steady state)")
    report_lines.append("  - 'P95 latency' is the 95th percentile (tail performance)")
    report_lines.append("=" * 80)

    report_text = "\n".join(report_lines)

    # Print to stdout
    print(report_text)

    # Write to file if specified
    if output_file:
        try:
            with open(output_file, 'w') as f:
                f.write(report_text)
            print(f"\nReport saved to: {output_file}")
        except Exception as e:
            print(f"Warning: Could not write report to {output_file}: {e}", file=sys.stderr)

    return report_text


def main():
    parser = argparse.ArgumentParser(
        description='Phase 3A Benchmark: Compare new vs legacy path performance'
    )
    parser.add_argument(
        '--url',
        default='http://127.0.0.1:5002',
        help='Base URL of Flask app (default: http://127.0.0.1:5002)'
    )
    parser.add_argument(
        '--iterations',
        type=int,
        default=20,
        help='Number of iterations per endpoint/mode (default: 20)'
    )
    parser.add_argument(
        '--report',
        default='benchmarks/PHASE3A_REPORT.txt',
        help='Output file for report (default: benchmarks/PHASE3A_REPORT.txt)'
    )

    args = parser.parse_args()

    # Validate base URL
    base_url = args.url.rstrip('/')
    print(f"Phase 3A Benchmark")
    print(f"Base URL: {base_url}")
    print(f"Iterations: {args.iterations}")
    print()

    # Get network data for testing
    network_name, network_id = get_network_data()
    if not network_name:
        print("Error: Could not find network data in database", file=sys.stderr)
        sys.exit(1)

    print(f"Using network: {network_name} (id={network_id})")
    print()

    # Benchmark endpoints
    results = {}
    endpoints = [
        '/api/funding-networks',
        '/api/funding-network-details/1',
        '/networks',
        f'/creator-network/{quote(network_name, safe="")}'
    ]

    for endpoint in endpoints:
        print(f"Benchmarking {endpoint}...")

        # Benchmark new path
        new_result = benchmark_endpoint(base_url, endpoint, 'new', args.iterations)
        if new_result:
            results[(endpoint, 'new')] = new_result
            print(f"  ✓ new path: {new_result['warm_avg']:.2f}ms avg")
        else:
            print(f"  ✗ new path: FAILED")

        # Benchmark legacy path
        legacy_result = benchmark_endpoint(base_url, endpoint, 'legacy', args.iterations)
        if legacy_result:
            results[(endpoint, 'legacy')] = legacy_result
            print(f"  ✓ legacy path: {legacy_result['warm_avg']:.2f}ms avg")
        else:
            print(f"  ✗ legacy path: FAILED")

        print()

    # Generate report
    print("\nGenerating report...")
    generate_report(results, network_name, network_id, args.iterations, args.report)

    print("\nBenchmark complete!")


if __name__ == '__main__':
    main()
