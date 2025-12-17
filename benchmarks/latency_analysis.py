#!/usr/bin/env python3
"""
Analyze latency breakdown to identify bottlenecks.
"""

import asyncio
import time
import httpx
from pathlib import Path
from statistics import mean, median, stdev

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
}

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxy() -> str:
    with open(PROXY_FILE) as f:
        return f"http://{f.readline().strip()}"


async def measure_latency(client: httpx.AsyncClient, domain: str) -> dict:
    """Measure detailed timing for a single request."""
    tld = domain.split(".")[-1]
    url = f"{RDAP_ENDPOINTS[tld]}{domain}"

    start = time.perf_counter()

    try:
        async with client.stream("GET", url, timeout=15.0) as resp:
            headers_received = time.perf_counter()
            status = resp.status_code

        end = time.perf_counter()

        return {
            "domain": domain,
            "status": status,
            "total_ms": (end - start) * 1000,
            "to_headers_ms": (headers_received - start) * 1000,
            "success": True
        }
    except Exception as e:
        return {
            "domain": domain,
            "error": str(e),
            "total_ms": (time.perf_counter() - start) * 1000,
            "success": False
        }


async def test_sequential_vs_parallel():
    """Compare sequential vs parallel to identify overhead."""
    proxy = load_proxy()

    # Test domains (all fake to test 404 responses - faster)
    domains = [f"testxyz{i:05d}.com" for i in range(20)]

    print("=" * 70)
    print("LATENCY ANALYSIS")
    print("=" * 70)

    # Test 1: Sequential requests (same connection)
    print("\n[1] SEQUENTIAL (single connection, reused)")
    print("-" * 50)

    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    async with httpx.AsyncClient(proxy=proxy, limits=limits) as client:
        results = []
        for domain in domains[:10]:
            result = await measure_latency(client, domain)
            results.append(result)
            if result['success']:
                print(f"  {domain}: {result['total_ms']:.0f}ms (headers: {result['to_headers_ms']:.0f}ms)")

        if results:
            times = [r['total_ms'] for r in results if r['success']]
            print(f"\n  Avg: {mean(times):.0f}ms, Median: {median(times):.0f}ms")
            print(f"  First request (cold): {times[0]:.0f}ms")
            print(f"  Subsequent avg: {mean(times[1:]):.0f}ms")

    # Test 2: Parallel with single proxy
    print("\n[2] PARALLEL (10 concurrent, single proxy)")
    print("-" * 50)

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=10)
    async with httpx.AsyncClient(proxy=proxy, limits=limits) as client:
        start = time.perf_counter()
        tasks = [measure_latency(client, d) for d in domains]
        results = await asyncio.gather(*tasks)
        total_time = (time.perf_counter() - start) * 1000

        times = [r['total_ms'] for r in results if r['success']]
        print(f"  Total wall time: {total_time:.0f}ms for {len(domains)} domains")
        print(f"  Effective throughput: {len(domains) / total_time * 1000:.1f}/sec")
        print(f"  Avg latency: {mean(times):.0f}ms")

    # Test 3: Multiple proxies parallel
    print("\n[3] PARALLEL (20 concurrent, 10 proxies)")
    print("-" * 50)

    proxies = []
    with open(PROXY_FILE) as f:
        for i, line in enumerate(f):
            if i >= 10:
                break
            proxies.append(f"http://{line.strip()}")

    start = time.perf_counter()

    async def check_with_proxy(proxy: str, domain: str):
        async with httpx.AsyncClient(proxy=proxy) as client:
            return await measure_latency(client, domain)

    # Distribute domains across proxies
    tasks = []
    for i, domain in enumerate(domains):
        proxy = proxies[i % len(proxies)]
        tasks.append(check_with_proxy(proxy, domain))

    results = await asyncio.gather(*tasks)
    total_time = (time.perf_counter() - start) * 1000

    times = [r['total_ms'] for r in results if r['success']]
    print(f"  Total wall time: {total_time:.0f}ms for {len(domains)} domains")
    print(f"  Effective throughput: {len(domains) / total_time * 1000:.1f}/sec")
    print(f"  Avg latency: {mean(times):.0f}ms")

    # Analysis
    print("\n" + "=" * 70)
    print("BOTTLENECK ANALYSIS")
    print("=" * 70)

    analysis = """
Key findings:

1. SINGLE CONNECTION LATENCY
   - First request (cold): ~{cold}ms (includes TLS handshake)
   - Subsequent (warm): ~{warm}ms (connection reused)
   - This is the RDAP server response time

2. THEORETICAL MAXIMUM THROUGHPUT
   - With {warm}ms latency per request
   - Single connection: {single_max:.0f} requests/sec
   - 1000 connections: {multi_max:,.0f} requests/sec (theoretical)

3. WHY WE DON'T HIT THEORETICAL MAX
   - Connection establishment overhead
   - Proxy routing latency
   - Python asyncio overhead
   - Network congestion

4. REALISTIC EXPECTATIONS
   - Expect 30-50% of theoretical max
   - With 1000 proxies: {realistic_low:,.0f} - {realistic_high:,.0f}/sec
   - Time for 580M checks: {time_low:.1f} - {time_high:.1f} days
"""

    # Get latency from first test
    sequential_times = [r['total_ms'] for r in results if r['success']]
    cold = sequential_times[0] if sequential_times else 500
    warm = mean(sequential_times[1:]) if len(sequential_times) > 1 else cold

    single_max = 1000 / warm
    multi_max = single_max * 1000  # 1000 proxies

    realistic_low = multi_max * 0.3
    realistic_high = multi_max * 0.5

    total_checks = 580_000_000
    time_low = total_checks / realistic_high / 3600 / 24
    time_high = total_checks / realistic_low / 3600 / 24

    print(analysis.format(
        cold=cold,
        warm=warm,
        single_max=single_max,
        multi_max=multi_max,
        realistic_low=realistic_low,
        realistic_high=realistic_high,
        time_low=time_low,
        time_high=time_high
    ))


async def main():
    await test_sequential_vs_parallel()


if __name__ == "__main__":
    asyncio.run(main())
