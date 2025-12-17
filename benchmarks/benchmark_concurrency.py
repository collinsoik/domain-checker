#!/usr/bin/env python3
"""
Benchmark concurrency limits and estimate completion time.
Tests actual throughput at various concurrency levels.
"""

import asyncio
import time
import resource
import sys
import httpx
from pathlib import Path
from itertools import cycle
from dataclasses import dataclass, field
from typing import List

RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain/",
}

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")

# Generate test domains
def generate_test_domains(count: int) -> List[str]:
    """Generate mix of real and fake domains for testing."""
    real = ["google.com", "amazon.com", "microsoft.com", "github.com", "netflix.com"]
    fake_base = ["testllc", "bizname", "acmecorp", "atlcompany", "gaservice"]
    tlds = ["com", "net", "org"]

    domains = []
    for i in range(count):
        if i % 10 == 0:  # 10% real domains
            domains.append(real[i % len(real)])
        else:
            base = fake_base[i % len(fake_base)]
            tld = tlds[i % len(tlds)]
            domains.append(f"{base}{i:06d}.{tld}")
    return domains


def load_proxies(limit: int = None) -> List[str]:
    """Load proxies from file."""
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
class BenchmarkStats:
    total: int = 0
    success: int = 0
    errors: int = 0
    timeouts: int = 0
    start_time: float = 0
    end_time: float = 0
    error_types: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def throughput(self) -> float:
        return self.total / self.duration if self.duration > 0 else 0


async def check_domain_stream(
    client: httpx.AsyncClient,
    domain: str,
    semaphore: asyncio.Semaphore,
    stats: BenchmarkStats
) -> str:
    """Optimized streaming check."""
    async with semaphore:
        tld = domain.split(".")[-1]
        url = f"{RDAP_ENDPOINTS.get(tld, RDAP_ENDPOINTS['com'])}{domain}"

        try:
            async with client.stream("GET", url, timeout=10.0) as response:
                stats.total += 1
                if response.status_code in (200, 404):
                    stats.success += 1
                    return "taken" if response.status_code == 200 else "available"
                else:
                    stats.errors += 1
                    return "error"
        except httpx.TimeoutException:
            stats.total += 1
            stats.timeouts += 1
            return "timeout"
        except Exception as e:
            stats.total += 1
            stats.errors += 1
            err_type = type(e).__name__
            stats.error_types[err_type] = stats.error_types.get(err_type, 0) + 1
            return "error"


async def benchmark_concurrency(
    domains: List[str],
    proxies: List[str],
    concurrency: int,
    connections_per_proxy: int = 10
) -> BenchmarkStats:
    """
    Benchmark with specific concurrency level.
    Uses connection pooling per proxy.
    """
    stats = BenchmarkStats()
    semaphore = asyncio.Semaphore(concurrency)

    # Distribute domains across proxies
    proxy_cycle = cycle(proxies)
    proxy_domains = {p: [] for p in proxies}
    for domain in domains:
        proxy = next(proxy_cycle)
        proxy_domains[proxy].append(domain)

    stats.start_time = time.time()

    async def process_proxy_batch(proxy: str, batch_domains: List[str]):
        """Process all domains for a single proxy with connection pooling."""
        limits = httpx.Limits(
            max_connections=connections_per_proxy,
            max_keepalive_connections=connections_per_proxy
        )
        async with httpx.AsyncClient(proxy=proxy, limits=limits) as client:
            tasks = [check_domain_stream(client, d, semaphore, stats) for d in batch_domains]
            await asyncio.gather(*tasks, return_exceptions=True)

    # Run all proxy batches concurrently
    await asyncio.gather(*[
        process_proxy_batch(proxy, batch_domains)
        for proxy, batch_domains in proxy_domains.items()
        if batch_domains
    ])

    stats.end_time = time.time()
    return stats


def check_system_limits():
    """Check and report system limits that affect concurrency."""
    print("=" * 70)
    print("SYSTEM LIMITS CHECK")
    print("=" * 70)

    # File descriptor limit
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    print(f"\nFile Descriptors:")
    print(f"  Soft limit: {soft:,}")
    print(f"  Hard limit: {hard:,}")
    print(f"  Note: Each connection uses ~1 FD. 1000 proxies × 10 conn = 10,000 FDs needed")

    if soft < 10000:
        print(f"  WARNING: Soft limit too low! Run: ulimit -n 10240")

    # Python version
    print(f"\nPython: {sys.version.split()[0]}")

    # asyncio info
    try:
        import uvloop
        print("uvloop: Available (faster event loop)")
    except ImportError:
        print("uvloop: Not installed (pip install uvloop for 20-30% speedup)")

    return soft


async def main():
    print("=" * 70)
    print("CONCURRENCY BENCHMARK")
    print("=" * 70)

    fd_limit = check_system_limits()

    # Load proxies
    proxies = load_proxies(limit=100)  # Use 100 proxies for test
    print(f"\nLoaded {len(proxies)} proxies for testing")

    # Test parameters
    test_sizes = [50, 100, 200, 500]
    concurrency_levels = [10, 25, 50, 100, 200]

    results = []

    print("\n" + "=" * 70)
    print("THROUGHPUT BY CONCURRENCY LEVEL")
    print("=" * 70)
    print(f"\n{'Domains':<10} {'Concurrency':<12} {'Time':>10} {'Throughput':>15} {'Errors':>10}")
    print("-" * 70)

    for num_domains in test_sizes:
        domains = generate_test_domains(num_domains)

        for concurrency in concurrency_levels:
            if concurrency > num_domains:
                continue

            stats = await benchmark_concurrency(
                domains,
                proxies,
                concurrency=concurrency,
                connections_per_proxy=min(10, concurrency // len(proxies) + 1)
            )

            results.append({
                'domains': num_domains,
                'concurrency': concurrency,
                'throughput': stats.throughput,
                'errors': stats.errors + stats.timeouts,
                'duration': stats.duration
            })

            print(f"{num_domains:<10} {concurrency:<12} {stats.duration:>8.2f}s {stats.throughput:>12.1f}/sec {stats.errors + stats.timeouts:>10}")

            # Small delay between tests
            await asyncio.sleep(0.5)

    # Find optimal concurrency
    best = max(results, key=lambda x: x['throughput'])

    print("\n" + "=" * 70)
    print("ANALYSIS & PROJECTIONS")
    print("=" * 70)

    print(f"\nBest throughput achieved: {best['throughput']:.1f} domains/sec")
    print(f"  at concurrency level: {best['concurrency']}")

    # Project to full scale
    total_checks = 580_000_000

    print(f"\n--- COMPLETION TIME ESTIMATES FOR {total_checks:,} CHECKS ---\n")

    # Conservative, realistic, optimistic scenarios
    scenarios = [
        ("Conservative (current test)", best['throughput']),
        ("With 1000 proxies (10x)", best['throughput'] * 10),
        ("With uvloop + tuning (15x)", best['throughput'] * 15),
        ("Optimistic max (20x)", best['throughput'] * 20),
    ]

    print(f"{'Scenario':<35} {'Throughput':>15} {'Time':>20}")
    print("-" * 70)

    for name, throughput in scenarios:
        seconds = total_checks / throughput
        hours = seconds / 3600
        days = hours / 24

        if days >= 1:
            time_str = f"{days:.1f} days ({hours:.0f}h)"
        else:
            time_str = f"{hours:.1f} hours"

        print(f"{name:<35} {throughput:>12.0f}/sec {time_str:>20}")

    # Bottleneck analysis
    print("\n" + "=" * 70)
    print("BOTTLENECK ANALYSIS")
    print("=" * 70)

    bottlenecks = """
1. FILE DESCRIPTORS (FDs)
   - Each TCP connection = 1 FD
   - 1000 proxies × 10 connections = 10,000 FDs needed
   - Fix: ulimit -n 10240 (or higher)
   - Current limit: {fd_limit:,}

2. PYTHON GIL (Global Interpreter Lock)
   - asyncio is single-threaded
   - CPU-bound work blocks the event loop
   - Fix: Use uvloop (20-30% faster), minimize CPU work

3. DNS RESOLUTION
   - Each new domain = DNS lookup
   - Can become bottleneck at high concurrency
   - Fix: Use DNS caching, or IP-based RDAP endpoints

4. CONNECTION ESTABLISHMENT
   - TLS handshake = ~100-200ms per new connection
   - Fix: Connection pooling (reuse connections per proxy)

5. RDAP SERVER RATE LIMITS
   - Verisign (.com/.net) may rate limit per IP
   - Fix: Distribute across 1000 proxies, respect rate limits

6. PROXY BANDWIDTH
   - Floxy: 250GB limit
   - Streaming approach: ~190GB needed (OK)

7. MEMORY
   - Each connection = ~10-50KB memory
   - 10,000 connections = ~100-500MB
   - Should be fine on modern systems
"""
    print(bottlenecks.format(fd_limit=fd_limit))

    # Recommendations
    print("=" * 70)
    print("RECOMMENDATIONS FOR FULL SCALE")
    print("=" * 70)
    recommendations = """
1. Increase file descriptor limit:
   ulimit -n 65536

2. Install uvloop for faster async:
   pip install uvloop

3. Use connection pooling:
   - 10-20 connections per proxy
   - Reuse connections for multiple domains

4. Optimal concurrency:
   - Start with 500-1000 concurrent requests
   - Monitor error rate, adjust down if needed
   - Target: 5,000-10,000 requests/sec

5. Implement circuit breakers:
   - Skip proxies with >5 consecutive failures
   - Re-enable after 60s cooldown

6. Checkpoint every 100K domains:
   - Allows resume on crash
   - Provides progress visibility
"""
    print(recommendations)


if __name__ == "__main__":
    asyncio.run(main())
