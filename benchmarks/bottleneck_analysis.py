#!/usr/bin/env python3
"""
Bottleneck Analysis: Why does throughput drop with more proxies?

Hypotheses:
1. Local machine limits (file descriptors, sockets)
2. Verisign rate limiting (global, not per-IP)
3. Proxy connection overhead (establishing 1000 vs 200 connections)
4. asyncio task scheduling overhead
5. Retry logic overhead
"""

import asyncio
import time
from pathlib import Path

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

from whois_checker import WHOISChecker, generate_test_domains
from proxy_pool import ProxyPool


async def test_concurrency_sweep():
    """Test different concurrency levels to find optimal throughput."""
    print("=" * 70)
    print("CONCURRENCY SWEEP TEST")
    print("Find optimal concurrency for maximum throughput")
    print("=" * 70)

    pool = ProxyPool(max_proxies=None)  # All proxies
    total_proxies = len(pool)
    print(f"\nTotal proxies available: {total_proxies}")

    # Test with 5000 domains for each concurrency level
    test_size = 5000
    domains = generate_test_domains(test_size)

    results = []

    # Test different total concurrency levels
    concurrency_levels = [100, 200, 500, 1000, 1500, 2000]

    for concurrency in concurrency_levels:
        print(f"\n--- Testing concurrency: {concurrency} ---")

        checker = WHOISChecker()
        proxies = pool.get_healthy_proxies()
        sem = asyncio.Semaphore(concurrency)

        async def check_with_sem(domain: str, proxy) -> tuple:
            async with sem:
                proxy_dict = proxy.to_dict()
                result = await checker.check_single_domain(domain, proxy_dict)
                return result

        # Create tasks
        tasks = []
        for i, domain in enumerate(domains):
            proxy = proxies[i % len(proxies)]
            tasks.append(check_with_sem(domain, proxy))

        # Run and measure
        start = time.perf_counter()
        task_results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        # Count results
        success = sum(1 for r in task_results if r.status in ("taken", "available"))
        errors = sum(1 for r in task_results if r.status == "error")
        throughput = test_size / elapsed

        results.append({
            "concurrency": concurrency,
            "throughput": throughput,
            "success_rate": success / test_size * 100,
            "errors": errors,
            "time": elapsed
        })

        print(f"  Throughput: {throughput:.0f}/sec")
        print(f"  Success: {success}/{test_size} ({success/test_size*100:.1f}%)")
        print(f"  Errors: {errors}")
        print(f"  Time: {elapsed:.2f}s")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n{'Concurrency':<15} {'Throughput':<15} {'Success %':<15} {'Time':<10}")
    print("-" * 55)
    for r in results:
        print(f"{r['concurrency']:<15} {r['throughput']:<15.0f} {r['success_rate']:<15.1f} {r['time']:<10.2f}")

    # Find optimal
    best = max(results, key=lambda x: x['throughput'] if x['success_rate'] > 95 else 0)
    print(f"\nOptimal concurrency: {best['concurrency']} ({best['throughput']:.0f}/sec at {best['success_rate']:.1f}% success)")


async def test_proxy_count_impact():
    """Test if using fewer proxies with higher per-proxy concurrency is better."""
    print("\n" + "=" * 70)
    print("PROXY COUNT IMPACT TEST")
    print("Compare: Many proxies × low concurrency vs Few proxies × high concurrency")
    print("=" * 70)

    test_size = 5000
    domains = generate_test_domains(test_size)
    target_concurrency = 500  # Keep total concurrency constant

    scenarios = [
        (50, 10),    # 50 proxies × 10 each = 500 concurrent
        (100, 5),    # 100 proxies × 5 each = 500 concurrent
        (250, 2),    # 250 proxies × 2 each = 500 concurrent
        (500, 1),    # 500 proxies × 1 each = 500 concurrent
    ]

    results = []

    for num_proxies, per_proxy in scenarios:
        print(f"\n--- {num_proxies} proxies × {per_proxy} each = {num_proxies * per_proxy} concurrent ---")

        pool = ProxyPool(max_proxies=num_proxies)
        checker = WHOISChecker()
        proxies = pool.get_healthy_proxies()
        sem = asyncio.Semaphore(num_proxies * per_proxy)

        async def check_with_sem(domain: str, proxy) -> tuple:
            async with sem:
                proxy_dict = proxy.to_dict()
                return await checker.check_single_domain(domain, proxy_dict)

        tasks = [check_with_sem(domain, proxies[i % len(proxies)]) for i, domain in enumerate(domains)]

        start = time.perf_counter()
        task_results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        success = sum(1 for r in task_results if r.status in ("taken", "available"))
        throughput = test_size / elapsed

        results.append({
            "proxies": num_proxies,
            "per_proxy": per_proxy,
            "throughput": throughput,
            "success_rate": success / test_size * 100,
            "time": elapsed
        })

        print(f"  Throughput: {throughput:.0f}/sec, Success: {success/test_size*100:.1f}%")

    print("\n" + "=" * 70)
    print("SUMMARY: Same total concurrency (500), different proxy counts")
    print("=" * 70)
    print(f"\n{'Proxies':<10} {'Per-Proxy':<12} {'Throughput':<15} {'Success %':<12}")
    print("-" * 50)
    for r in results:
        print(f"{r['proxies']:<10} {r['per_proxy']:<12} {r['throughput']:<15.0f} {r['success_rate']:<12.1f}")


async def test_verisign_rate_limit():
    """Test if Verisign has a global rate limit regardless of source IP."""
    print("\n" + "=" * 70)
    print("VERISIGN RATE LIMIT TEST")
    print("Measure if sustained high throughput causes throttling")
    print("=" * 70)

    pool = ProxyPool(max_proxies=200)
    checker = WHOISChecker()
    proxies = pool.get_healthy_proxies()

    # Run 10 batches of 1000 domains each
    batch_size = 1000
    num_batches = 10
    concurrency = 500

    print(f"\nRunning {num_batches} batches of {batch_size} domains")
    print(f"Concurrency: {concurrency}")

    batch_results = []

    for batch_num in range(num_batches):
        domains = generate_test_domains(batch_size)
        sem = asyncio.Semaphore(concurrency)

        async def check_with_sem(domain: str, proxy):
            async with sem:
                return await checker.check_single_domain(domain, proxy.to_dict())

        tasks = [check_with_sem(domain, proxies[i % len(proxies)]) for i, domain in enumerate(domains)]

        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        success = sum(1 for r in results if r.status in ("taken", "available"))
        throughput = batch_size / elapsed

        batch_results.append({
            "batch": batch_num + 1,
            "throughput": throughput,
            "success_rate": success / batch_size * 100
        })

        print(f"Batch {batch_num + 1}: {throughput:.0f}/sec, {success/batch_size*100:.1f}% success")

    # Check if throughput degrades over time
    first_half = sum(r['throughput'] for r in batch_results[:5]) / 5
    second_half = sum(r['throughput'] for r in batch_results[5:]) / 5

    print(f"\nFirst 5 batches avg: {first_half:.0f}/sec")
    print(f"Last 5 batches avg: {second_half:.0f}/sec")
    print(f"Degradation: {(first_half - second_half) / first_half * 100:.1f}%")

    if second_half < first_half * 0.8:
        print("\n⚠️ RATE LIMITING DETECTED: Throughput drops over time")
    else:
        print("\n✓ No significant rate limiting detected")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        test = sys.argv[1]
        if test == "concurrency":
            asyncio.run(test_concurrency_sweep())
        elif test == "proxies":
            asyncio.run(test_proxy_count_impact())
        elif test == "ratelimit":
            asyncio.run(test_verisign_rate_limit())
        else:
            print(f"Unknown test: {test}")
    else:
        print("Bottleneck Analysis Tests")
        print("Usage: python bottleneck_analysis.py <test>")
        print()
        print("Available tests:")
        print("  concurrency  - Find optimal concurrency level")
        print("  proxies      - Compare proxy count impact")
        print("  ratelimit    - Check for Verisign rate limiting")
