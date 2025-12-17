#!/usr/bin/env python3
"""
High-concurrency stress test with uvloop.
Tests realistic throughput with 500+ proxies.
"""

import asyncio
import time
import httpx
from pathlib import Path
from itertools import cycle
from dataclasses import dataclass, field

# Use uvloop for better performance
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP_ENABLED = True
except ImportError:
    UVLOOP_ENABLED = False

RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain/",
}

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def generate_domains(count: int) -> list[str]:
    """Generate test domains."""
    real = ["google.com", "amazon.com", "facebook.com", "microsoft.com", "apple.com"]
    tlds = ["com", "net", "org"]
    domains = []
    for i in range(count):
        if i % 20 == 0:
            domains.append(real[i % len(real)])
        else:
            tld = tlds[i % len(tlds)]
            domains.append(f"testbiz{i:08d}.{tld}")
    return domains


def load_proxies(limit: int = None) -> list[str]:
    proxies = []
    with open(PROXY_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                proxies.append(f"http://{line}")
                if limit and len(proxies) >= limit:
                    break
    return proxies


@dataclass
class Stats:
    total: int = 0
    success: int = 0
    taken: int = 0
    available: int = 0
    errors: int = 0
    timeouts: int = 0


async def check_domain(client: httpx.AsyncClient, domain: str, sem: asyncio.Semaphore, stats: Stats):
    """Streaming check with semaphore."""
    async with sem:
        tld = domain.split(".")[-1]
        url = f"{RDAP_ENDPOINTS.get(tld, RDAP_ENDPOINTS['com'])}{domain}"
        try:
            async with client.stream("GET", url, timeout=8.0) as resp:
                stats.total += 1
                if resp.status_code == 200:
                    stats.taken += 1
                    stats.success += 1
                elif resp.status_code == 404:
                    stats.available += 1
                    stats.success += 1
                else:
                    stats.errors += 1
        except httpx.TimeoutException:
            stats.total += 1
            stats.timeouts += 1
        except Exception:
            stats.total += 1
            stats.errors += 1


async def run_stress_test(num_domains: int, num_proxies: int, concurrency: int):
    """Run stress test with given parameters."""
    domains = generate_domains(num_domains)
    proxies = load_proxies(limit=num_proxies)

    print(f"\nTest: {num_domains} domains, {len(proxies)} proxies, {concurrency} concurrent")
    print("-" * 60)

    stats = Stats()
    sem = asyncio.Semaphore(concurrency)

    # Group domains by proxy
    proxy_cycle = cycle(proxies)
    proxy_domains = {p: [] for p in proxies}
    for d in domains:
        proxy_domains[next(proxy_cycle)].append(d)

    start = time.time()

    async def process_proxy(proxy: str, doms: list[str]):
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=20)
        async with httpx.AsyncClient(proxy=proxy, limits=limits) as client:
            tasks = [check_domain(client, d, sem, stats) for d in doms]
            await asyncio.gather(*tasks, return_exceptions=True)

    await asyncio.gather(*[
        process_proxy(p, d) for p, d in proxy_domains.items() if d
    ])

    elapsed = time.time() - start
    throughput = stats.total / elapsed

    print(f"Completed: {stats.total} domains in {elapsed:.2f}s")
    print(f"Throughput: {throughput:.1f} domains/sec")
    print(f"Results: {stats.taken} taken, {stats.available} available")
    print(f"Errors: {stats.errors} errors, {stats.timeouts} timeouts")
    print(f"Success rate: {stats.success/stats.total*100:.1f}%")

    return throughput


async def main():
    print("=" * 70)
    print("HIGH-CONCURRENCY STRESS TEST")
    print("=" * 70)
    print(f"uvloop: {'ENABLED' if UVLOOP_ENABLED else 'NOT AVAILABLE'}")

    # Progressive stress test
    test_configs = [
        # (domains, proxies, concurrency)
        (100, 50, 100),
        (200, 100, 200),
        (500, 200, 400),
        (1000, 500, 500),
        (1000, 500, 1000),
        (2000, 1000, 1000),
    ]

    results = []

    for num_domains, num_proxies, concurrency in test_configs:
        try:
            throughput = await run_stress_test(num_domains, num_proxies, concurrency)
            results.append({
                'domains': num_domains,
                'proxies': num_proxies,
                'concurrency': concurrency,
                'throughput': throughput
            })
        except Exception as e:
            print(f"Test failed: {e}")
            results.append({
                'domains': num_domains,
                'proxies': num_proxies,
                'concurrency': concurrency,
                'throughput': 0
            })

        await asyncio.sleep(1)  # Cool down

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n{'Domains':<10} {'Proxies':<10} {'Concurrency':<12} {'Throughput':>15}")
    print("-" * 50)
    for r in results:
        print(f"{r['domains']:<10} {r['proxies']:<10} {r['concurrency']:<12} {r['throughput']:>12.1f}/sec")

    # Best result
    best = max(results, key=lambda x: x['throughput'])
    print(f"\nBest: {best['throughput']:.1f}/sec with {best['proxies']} proxies @ {best['concurrency']} concurrency")

    # Time estimates for 580M checks
    print("\n" + "=" * 70)
    print("TIME ESTIMATES FOR 580,000,000 CHECKS")
    print("=" * 70)

    total = 580_000_000
    for throughput, label in [
        (best['throughput'], "Current best"),
        (best['throughput'] * 2, "2x optimization"),
        (best['throughput'] * 5, "5x (theoretical max)"),
        (1000, "1,000/sec baseline"),
        (5000, "5,000/sec target"),
        (10000, "10,000/sec optimistic"),
    ]:
        hours = total / throughput / 3600
        days = hours / 24
        print(f"{label:<25} {throughput:>10,.0f}/sec → {days:>6.1f} days ({hours:>6.0f} hours)")


if __name__ == "__main__":
    asyncio.run(main())
