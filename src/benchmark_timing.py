#!/usr/bin/env python3
"""
Benchmark timing script to identify bottlenecks in domain checking.

Supports both WHOIS and RDAP protocols.

Measures:
- Connection time to proxy
- Tunnel/Request establishment time
- Query time
- Total end-to-end time
"""

import asyncio
import argparse
import base64
import time
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP = True
except ImportError:
    UVLOOP = False

# WHOIS Configuration
WHOIS_SERVER = "whois.verisign-grs.com"
WHOIS_PORT = 43
RESPONSE_BYTES = 64
TIMEOUT = 10

# RDAP Configuration
RDAP_ENDPOINT = "https://rdap.verisign.com/com/v1/domain/"


@dataclass
class TimingResult:
    domain: str
    status: str  # 'taken', 'available', 'error', 'timeout'
    connect_ms: float = 0
    tunnel_ms: float = 0
    query_ms: float = 0
    total_ms: float = 0
    error: Optional[str] = None


def load_proxies(proxy_file: Path, max_proxies: Optional[int] = None) -> list[dict]:
    """Load proxies from file."""
    proxies = []
    with open(proxy_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            auth, hostport = line.split("@")
            user, passwd = auth.split(":")
            host, port = hostport.split(":")
            proxies.append({"host": host, "port": int(port), "user": user, "pass": passwd})
            if max_proxies and len(proxies) >= max_proxies:
                break
    return proxies


async def check_domain_timed(domain: str, proxy: dict) -> TimingResult:
    """Check single domain with detailed timing."""
    total_start = time.perf_counter()
    connect_ms = 0
    tunnel_ms = 0
    query_ms = 0

    try:
        # Phase 1: Connect to proxy
        connect_start = time.perf_counter()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy["host"], proxy["port"]),
            timeout=TIMEOUT
        )
        connect_ms = (time.perf_counter() - connect_start) * 1000

        # Phase 2: Establish tunnel
        tunnel_start = time.perf_counter()
        auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
        connect_req = (
            f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n"
            f"\r\n"
        )
        writer.write(connect_req.encode())
        await writer.drain()

        # Read CONNECT response
        response = await asyncio.wait_for(reader.readline(), timeout=TIMEOUT)
        if b"200" not in response:
            writer.close()
            total_ms = (time.perf_counter() - total_start) * 1000
            return TimingResult(domain, "error", connect_ms, 0, 0, total_ms, f"CONNECT failed: {response.decode().strip()}")

        # Drain remaining headers
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        tunnel_ms = (time.perf_counter() - tunnel_start) * 1000

        # Phase 3: WHOIS query
        query_start = time.perf_counter()
        query = f"{domain}\r\n"
        writer.write(query.encode())
        await writer.drain()

        # Read response
        response = await asyncio.wait_for(
            reader.read(RESPONSE_BYTES),
            timeout=TIMEOUT
        )
        query_ms = (time.perf_counter() - query_start) * 1000

        # Close connection
        writer.close()
        try:
            await writer.wait_closed()
        except:
            pass

        total_ms = (time.perf_counter() - total_start) * 1000

        # Detect status
        if b"No match" in response:
            return TimingResult(domain, "available", connect_ms, tunnel_ms, query_ms, total_ms)
        elif b"Domain Name" in response:
            return TimingResult(domain, "taken", connect_ms, tunnel_ms, query_ms, total_ms)
        else:
            return TimingResult(domain, "unknown", connect_ms, tunnel_ms, query_ms, total_ms, f"Response: {response[:30]}")

    except asyncio.TimeoutError:
        total_ms = (time.perf_counter() - total_start) * 1000
        return TimingResult(domain, "timeout", connect_ms, tunnel_ms, query_ms, total_ms, "timeout")
    except Exception as e:
        total_ms = (time.perf_counter() - total_start) * 1000
        return TimingResult(domain, "error", connect_ms, tunnel_ms, query_ms, total_ms, str(e))


async def check_domain_rdap_timed(domain: str, proxy: dict, client: httpx.AsyncClient) -> TimingResult:
    """Check single domain via RDAP with detailed timing."""
    total_start = time.perf_counter()
    connect_ms = 0
    request_ms = 0
    query_ms = 0

    url = f"{RDAP_ENDPOINT}{domain}"

    try:
        # Phase 1: Send request (includes connection if not pooled)
        request_start = time.perf_counter()
        response = await client.get(url, timeout=TIMEOUT)
        query_ms = (time.perf_counter() - request_start) * 1000

        total_ms = (time.perf_counter() - total_start) * 1000

        # Detect status from HTTP code
        if response.status_code == 404:
            return TimingResult(domain, "available", 0, 0, query_ms, total_ms)
        elif response.status_code == 200:
            return TimingResult(domain, "taken", 0, 0, query_ms, total_ms)
        elif response.status_code == 429:
            return TimingResult(domain, "rate_limited", 0, 0, query_ms, total_ms, "429 Too Many Requests")
        else:
            return TimingResult(domain, "error", 0, 0, query_ms, total_ms, f"HTTP {response.status_code}")

    except httpx.TimeoutException:
        total_ms = (time.perf_counter() - total_start) * 1000
        return TimingResult(domain, "timeout", 0, 0, query_ms, total_ms, "timeout")
    except Exception as e:
        total_ms = (time.perf_counter() - total_start) * 1000
        return TimingResult(domain, "error", 0, 0, query_ms, total_ms, str(e))


def generate_test_domains(count: int) -> list[str]:
    """Generate mix of real and fake domains for testing."""
    real = ["google.com", "amazon.com", "microsoft.com", "github.com", "apple.com"]
    domains = []
    for i in range(count):
        if i % 10 == 0:  # 10% real domains
            domains.append(real[i % len(real)])
        else:
            domains.append(f"testxyz{i:08d}.com")
    return domains


def print_timing_stats(results: list[TimingResult]):
    """Print timing statistics."""
    # Filter successful results for timing stats
    successful = [r for r in results if r.status in ("taken", "available")]
    all_results = results

    if not successful:
        print("No successful results to analyze!")
        return

    # Collect timing data
    connect_times = [r.connect_ms for r in successful]
    tunnel_times = [r.tunnel_ms for r in successful]
    query_times = [r.query_ms for r in successful]
    total_times = [r.total_ms for r in successful]

    def percentiles(data):
        if not data:
            return {"p50": 0, "p95": 0, "p99": 0, "max": 0, "avg": 0}
        sorted_data = sorted(data)
        n = len(sorted_data)
        return {
            "p50": sorted_data[int(n * 0.5)],
            "p95": sorted_data[int(n * 0.95)] if n >= 20 else sorted_data[-1],
            "p99": sorted_data[int(n * 0.99)] if n >= 100 else sorted_data[-1],
            "max": sorted_data[-1],
            "avg": statistics.mean(data)
        }

    print("\n" + "=" * 70)
    print("TIMING BREAKDOWN (milliseconds)")
    print("=" * 70)
    print(f"{'Phase':<15} {'Avg':>10} {'P50':>10} {'P95':>10} {'P99':>10} {'Max':>10}")
    print("-" * 70)

    for name, times in [
        ("Connect", connect_times),
        ("Tunnel", tunnel_times),
        ("Query", query_times),
        ("Total", total_times)
    ]:
        p = percentiles(times)
        print(f"{name:<15} {p['avg']:>10.1f} {p['p50']:>10.1f} {p['p95']:>10.1f} {p['p99']:>10.1f} {p['max']:>10.1f}")

    print("\n" + "=" * 70)
    print("STATUS BREAKDOWN")
    print("=" * 70)

    status_counts = {}
    for r in all_results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        pct = count / len(all_results) * 100
        print(f"  {status:<12}: {count:>6} ({pct:>5.1f}%)")

    # Time distribution
    print("\n" + "=" * 70)
    print("TIME DISTRIBUTION (total time)")
    print("=" * 70)

    buckets = [
        (0, 100, "0-100ms"),
        (100, 500, "100-500ms"),
        (500, 1000, "500ms-1s"),
        (1000, 5000, "1-5s"),
        (5000, 10000, "5-10s"),
        (10000, float('inf'), ">10s")
    ]

    for low, high, label in buckets:
        count = sum(1 for r in all_results if low <= r.total_ms < high)
        pct = count / len(all_results) * 100
        bar = "#" * int(pct / 2)
        print(f"  {label:<12}: {count:>6} ({pct:>5.1f}%) {bar}")

    # Error analysis
    errors = [r for r in all_results if r.status in ("error", "timeout")]
    if errors:
        print("\n" + "=" * 70)
        print("ERROR ANALYSIS")
        print("=" * 70)
        error_types = {}
        for r in errors:
            err = r.error or "unknown"
            err_key = err[:50]  # Truncate long errors
            error_types[err_key] = error_types.get(err_key, 0) + 1

        for err, count in sorted(error_types.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>4}x: {err}")


async def run_benchmark(
    num_domains: int,
    num_proxies: int,
    concurrency: int,
    proxy_file: Path,
    protocol: str = "whois"
):
    """Run the timing benchmark."""
    print("=" * 70)
    print(f"DOMAIN CHECKER TIMING BENCHMARK ({protocol.upper()})")
    print("=" * 70)
    print(f"Protocol: {protocol.upper()}")
    print(f"uvloop: {'enabled' if UVLOOP else 'disabled'}")
    print(f"Domains: {num_domains}")
    print(f"Proxies: {num_proxies}")
    print(f"Concurrency: {concurrency}")
    print(f"Timeout: {TIMEOUT}s")
    print()

    # Load proxies
    proxies = load_proxies(proxy_file, num_proxies)
    print(f"Loaded {len(proxies)} proxies")

    # Generate domains
    domains = generate_test_domains(num_domains)

    # Create semaphore for concurrency control
    sem = asyncio.Semaphore(concurrency)

    if protocol == "whois":
        async def check_with_sem(domain: str, proxy_idx: int) -> TimingResult:
            async with sem:
                proxy = proxies[proxy_idx % len(proxies)]
                return await check_domain_timed(domain, proxy)

        # Run benchmark
        print(f"\nRunning WHOIS benchmark...")
        start = time.perf_counter()

        tasks = [check_with_sem(domain, i) for i, domain in enumerate(domains)]
        results = await asyncio.gather(*tasks)

    else:  # RDAP
        # Group domains by proxy for connection pooling
        proxy_domains = {}
        for i, domain in enumerate(domains):
            proxy_idx = i % len(proxies)
            if proxy_idx not in proxy_domains:
                proxy_domains[proxy_idx] = []
            proxy_domains[proxy_idx].append(domain)

        async def check_proxy_group(proxy_idx: int, group_domains: list[str]) -> list[TimingResult]:
            proxy = proxies[proxy_idx]
            proxy_url = f"http://{proxy['user']}:{proxy['pass']}@{proxy['host']}:{proxy['port']}"
            results = []
            async with httpx.AsyncClient(proxy=proxy_url) as client:
                for domain in group_domains:
                    async with sem:
                        result = await check_domain_rdap_timed(domain, proxy, client)
                        results.append(result)
            return results

        # Run benchmark
        print(f"\nRunning RDAP benchmark...")
        start = time.perf_counter()

        tasks = [check_proxy_group(idx, doms) for idx, doms in proxy_domains.items()]
        all_results = await asyncio.gather(*tasks)
        results = [r for group in all_results for r in group]

    elapsed = time.perf_counter() - start
    throughput = num_domains / elapsed

    print(f"\nCompleted in {elapsed:.2f}s")
    print(f"Throughput: {throughput:.1f} domains/sec")

    # Print timing stats
    print_timing_stats(results)

    # Projection
    print("\n" + "=" * 70)
    print("PROJECTION")
    print("=" * 70)

    target_domains = 704_000_000
    if throughput > 0:
        hours = target_domains / throughput / 3600
        days = hours / 24
        print(f"At {throughput:.0f} domains/sec:")
        print(f"  704M domains = {hours:.1f} hours ({days:.2f} days)")

        target_rate = 3500  # Target for 2-3 days
        print(f"\nTarget rate for 2-3 days: {target_rate} domains/sec")
        print(f"Current rate: {throughput:.0f} domains/sec")
        print(f"Gap: {target_rate / throughput:.1f}x slower than target" if throughput < target_rate else "On target!")

    return results


async def test_single_proxy(proxy_file: Path, num_queries: int = 20):
    """Test a single proxy to establish baseline performance."""
    print("=" * 70)
    print("SINGLE PROXY BASELINE TEST")
    print("=" * 70)

    proxies = load_proxies(proxy_file, 1)
    proxy = proxies[0]
    print(f"Testing proxy: {proxy['host']}:{proxy['port']}")
    print(f"Queries: {num_queries} (sequential)")
    print()

    domains = generate_test_domains(num_queries)
    results = []

    for domain in domains:
        result = await check_domain_timed(domain, proxy)
        results.append(result)
        status = result.status[0].upper()
        print(f"  [{status}] {result.total_ms:>7.1f}ms (conn:{result.connect_ms:.0f} tun:{result.tunnel_ms:.0f} qry:{result.query_ms:.0f}) {domain[:30]}")

    print_timing_stats(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Domain Checker Timing Benchmark")
    parser.add_argument("--domains", type=int, default=1000, help="Number of domains to check")
    parser.add_argument("--proxies", type=int, default=100, help="Number of proxies to use")
    parser.add_argument("--concurrency", type=int, default=100, help="Max concurrent requests")
    parser.add_argument("--proxy-file", type=str, default="../data/proxies.txt", help="Path to proxy file")
    parser.add_argument("--protocol", type=str, default="whois", choices=["whois", "rdap"], help="Protocol to use")
    parser.add_argument("--single", action="store_true", help="Test single proxy baseline")

    args = parser.parse_args()

    proxy_file = Path(args.proxy_file)
    if not proxy_file.is_absolute():
        proxy_file = Path(__file__).parent / args.proxy_file

    if args.single:
        asyncio.run(test_single_proxy(proxy_file))
    else:
        asyncio.run(run_benchmark(
            args.domains,
            args.proxies,
            args.concurrency,
            proxy_file,
            args.protocol
        ))
