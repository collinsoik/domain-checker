#!/usr/bin/env python3
"""
Iteration 1: Optimized WHOIS Checker

NOTE: WHOIS does NOT support connection reuse - server closes after each query.
Each query requires a new CONNECT tunnel.

Optimizations:
- Minimal response: Read only 64 bytes (enough to detect status)
- Early detection: "No match" (available) or "Domain Name" (taken)
- Parallel queries with asyncio.gather
"""

import asyncio
import argparse
import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP = True
except ImportError:
    UVLOOP = False

# Configuration
WHOIS_SERVER = "whois.verisign-grs.com"
WHOIS_PORT = 43
RESPONSE_BYTES = 64  # Need enough to detect "No match for" or "Domain Name"
TIMEOUT = 10

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


@dataclass
class Result:
    domain: str
    status: str  # 'taken', 'available', 'error', 'unknown'
    error: Optional[str] = None


@dataclass
class Stats:
    total: int = 0
    taken: int = 0
    available: int = 0
    errors: int = 0
    unknown: int = 0
    tunnels_created: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0


def load_proxy() -> dict:
    """Load first proxy from file."""
    with open(PROXY_FILE) as f:
        line = f.readline().strip()
        auth, hostport = line.split("@")
        user, passwd = auth.split(":")
        host, port = hostport.split(":")
        return {"host": host, "port": int(port), "user": user, "pass": passwd}


class WHOISChecker:
    """Optimized WHOIS checker with connection reuse."""

    def __init__(self):
        self.stats = Stats()

    async def create_tunnel(self, proxy: dict) -> tuple:
        """Create HTTP CONNECT tunnel to WHOIS server."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy["host"], proxy["port"]),
            timeout=TIMEOUT
        )

        # Minimal CONNECT request
        auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
        connect_req = (
            f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n"
            f"\r\n"
        )

        writer.write(connect_req.encode())
        await writer.drain()
        self.stats.bytes_sent += len(connect_req)

        # Read CONNECT response
        response = await asyncio.wait_for(reader.readline(), timeout=TIMEOUT)
        self.stats.bytes_received += len(response)

        if b"200" not in response:
            writer.close()
            raise ConnectionError(f"CONNECT failed: {response.decode().strip()}")

        # Drain remaining headers
        while True:
            line = await reader.readline()
            self.stats.bytes_received += len(line)
            if line in (b"\r\n", b"\n", b""):
                break

        self.stats.tunnels_created += 1
        return reader, writer

    async def check_single(self, reader, writer, domain: str) -> Result:
        """Check single domain through existing tunnel."""
        query = f"{domain}\r\n"

        try:
            # Send query
            writer.write(query.encode())
            await writer.drain()
            self.stats.bytes_sent += len(query)

            # Read minimal response (32 bytes)
            response = await asyncio.wait_for(
                reader.read(RESPONSE_BYTES),
                timeout=TIMEOUT
            )
            self.stats.bytes_received += len(response)
            self.stats.total += 1

            # Detect status from first bytes
            if b"No match" in response:
                self.stats.available += 1
                return Result(domain, "available")
            elif b"Domain Name" in response:
                self.stats.taken += 1
                return Result(domain, "taken")
            else:
                self.stats.unknown += 1
                return Result(domain, "unknown", f"Unexpected: {response[:20]}")

        except asyncio.TimeoutError:
            self.stats.errors += 1
            self.stats.total += 1
            return Result(domain, "error", "timeout")
        except Exception as e:
            self.stats.errors += 1
            self.stats.total += 1
            return Result(domain, "error", str(e))

    async def check_single_domain(self, domain: str, proxy: dict) -> Result:
        """
        Check single domain - creates new tunnel for each query.
        WHOIS does not support connection reuse.
        """
        try:
            # Create tunnel
            reader, writer = await self.create_tunnel(proxy)

            # Send query
            query = f"{domain}\r\n"
            writer.write(query.encode())
            await writer.drain()
            self.stats.bytes_sent += len(query)

            # Read response
            response = await asyncio.wait_for(
                reader.read(RESPONSE_BYTES),
                timeout=TIMEOUT
            )
            self.stats.bytes_received += len(response)

            # Close tunnel
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass

            self.stats.total += 1

            # Detect status
            if b"No match" in response:
                self.stats.available += 1
                return Result(domain, "available")
            elif b"Domain Name" in response:
                self.stats.taken += 1
                return Result(domain, "taken")
            else:
                self.stats.unknown += 1
                return Result(domain, "unknown", f"Response: {response[:30]}")

        except asyncio.TimeoutError:
            self.stats.total += 1
            self.stats.errors += 1
            return Result(domain, "error", "timeout")
        except Exception as e:
            self.stats.total += 1
            self.stats.errors += 1
            return Result(domain, "error", str(e))

    async def check_batch(self, domains: list[str], proxy: dict) -> list[Result]:
        """
        Check batch of domains sequentially.
        Each query uses a new tunnel (WHOIS doesn't support reuse).
        """
        results = []
        for domain in domains:
            result = await self.check_single_domain(domain, proxy)
            results.append(result)
        return results

    async def check_batch_parallel(self, domains: list[str], proxy: dict, concurrency: int = 10) -> list[Result]:
        """
        Check batch of domains in parallel with semaphore.
        """
        sem = asyncio.Semaphore(concurrency)

        async def check_with_sem(domain: str) -> Result:
            async with sem:
                return await self.check_single_domain(domain, proxy)

        tasks = [check_with_sem(d) for d in domains]
        return await asyncio.gather(*tasks)


def generate_test_domains(count: int) -> list[str]:
    """Generate mix of real and fake domains for testing."""
    real = ["google.com", "amazon.com", "microsoft.com", "github.com", "apple.com"]
    domains = []

    for i in range(count):
        if i % 10 == 0:  # 10% real domains (should be taken)
            domains.append(real[i % len(real)])
        else:
            domains.append(f"testllc{i:08d}.com")

    return domains


async def run_test(num_domains: int, concurrency: int = 10):
    """Run Iteration 1 test: single proxy, parallel queries."""
    print("=" * 60)
    print("ITERATION 1: Core WHOIS Checker Test")
    print("=" * 60)
    print(f"uvloop: {'enabled' if UVLOOP else 'not available'}")
    print(f"Domains: {num_domains}")
    print(f"Concurrency: {concurrency}")
    print(f"Response bytes: {RESPONSE_BYTES}")
    print()

    # Load proxy
    proxy = load_proxy()
    print(f"Proxy: {proxy['host']}:{proxy['port']}")

    # Generate test domains
    domains = generate_test_domains(num_domains)

    # Create checker
    checker = WHOISChecker()

    # Run check with parallel execution
    print(f"\nChecking {num_domains} domains (concurrency={concurrency})...")
    print("-" * 60)

    start = time.perf_counter()
    results = await checker.check_batch_parallel(domains, proxy, concurrency=concurrency)
    elapsed = time.perf_counter() - start

    # Print sample results
    print(f"\nSample results (first 10):")
    for r in results[:10]:
        status_icon = {"taken": "[T]", "available": "[A]", "error": "[E]", "unknown": "[?]"}.get(r.status, "[?]")
        err = f" ({r.error})" if r.error else ""
        print(f"  {status_icon} {r.domain}{err}")

    # Stats
    stats = checker.stats
    print(f"\n{'=' * 60}")
    print("RESULTS")
    print("=" * 60)
    print(f"Total checked:    {stats.total}")
    print(f"Taken:            {stats.taken}")
    print(f"Available:        {stats.available}")
    print(f"Unknown:          {stats.unknown}")
    print(f"Errors:           {stats.errors}")
    print()
    print(f"Tunnels created:  {stats.tunnels_created}")
    print(f"Tunnels/query:    {stats.tunnels_created / stats.total:.2f}" if stats.total else "N/A")
    print()
    print(f"Bytes sent:       {stats.bytes_sent:,}")
    print(f"Bytes received:   {stats.bytes_received:,}")
    print(f"Total bandwidth:  {stats.bytes_sent + stats.bytes_received:,} bytes")
    print(f"Per query:        {(stats.bytes_sent + stats.bytes_received) / stats.total:.1f} bytes" if stats.total else "N/A")
    print()
    print(f"Time:             {elapsed:.2f}s")
    print(f"Throughput:       {stats.total / elapsed:.1f} domains/sec")

    # Verify expectations
    print(f"\n{'=' * 60}")
    print("VERIFICATION")
    print("=" * 60)

    # Each query needs its own tunnel (no reuse)
    print(f"Tunnels = Queries: {stats.tunnels_created} = {stats.total} {'✓' if stats.tunnels_created == stats.total else '✗'}")

    # ~300-400 bytes per query without reuse
    expected_bandwidth = num_domains * 350
    actual_bandwidth = stats.bytes_sent + stats.bytes_received
    print(f"Expected bandwidth: ~{expected_bandwidth:,} bytes")
    print(f"Actual bandwidth:   {actual_bandwidth:,} bytes {'✓' if actual_bandwidth < expected_bandwidth * 1.5 else '✗'}")

    success_rate = (stats.taken + stats.available) / stats.total * 100 if stats.total else 0
    print(f"Success rate: {success_rate:.1f}% {'✓' if success_rate > 90 else '✗'}")

    # Scale projections
    print(f"\n{'=' * 60}")
    print("SCALE PROJECTIONS")
    print("=" * 60)
    bytes_per_query = (stats.bytes_sent + stats.bytes_received) / stats.total if stats.total else 0
    total_checks = 580_000_000
    projected_bandwidth_gb = (bytes_per_query * total_checks) / (1024**3)
    print(f"At {bytes_per_query:.0f} bytes/query:")
    print(f"  580M checks = {projected_bandwidth_gb:.1f} GB")
    print(f"  Floxy limit: 250 GB")
    print(f"  Status: {'✓ OK' if projected_bandwidth_gb < 250 else '✗ OVER LIMIT'}")

    return results


async def run_test_multiproxy(num_domains: int, num_proxies: int = 50, concurrency_per_proxy: int = 10):
    """Run Iteration 2 test: multiple proxies, parallel queries."""
    from proxy_pool import ProxyPool

    print("=" * 60)
    print("ITERATION 2: Multi-Proxy WHOIS Checker Test")
    print("=" * 60)
    print(f"uvloop: {'enabled' if UVLOOP else 'not available'}")
    print(f"Domains: {num_domains}")
    print(f"Proxies: {num_proxies}")
    print(f"Concurrency per proxy: {concurrency_per_proxy}")
    print(f"Total concurrency: {num_proxies * concurrency_per_proxy}")
    print(f"Response bytes: {RESPONSE_BYTES}")
    print()

    # Load proxy pool
    pool = ProxyPool(max_proxies=num_proxies)
    print(f"Loaded {len(pool)} proxies")

    # Generate test domains
    domains = generate_test_domains(num_domains)

    # Create checker
    checker = WHOISChecker()

    # Distribute domains across proxies
    domains_per_proxy = max(1, num_domains // num_proxies)
    batches = pool.distribute_domains(domains, domains_per_proxy=domains_per_proxy)

    print(f"\nDistributed {num_domains} domains across {len(batches)} batches")
    print(f"Domains per batch: ~{domains_per_proxy}")
    print("-" * 60)

    start = time.perf_counter()

    # Create tasks for all batches
    async def check_batch_with_proxy(domain_batch: list[str], proxy) -> list[Result]:
        proxy_dict = proxy.to_dict()
        results = []
        for domain in domain_batch:
            result = await checker.check_single_domain(domain, proxy_dict)
            if result.status in ("taken", "available"):
                pool.report_success(proxy)
            else:
                pool.report_failure(proxy)
            results.append(result)
        return results

    # Use semaphore to limit total concurrent connections
    total_concurrency = num_proxies * concurrency_per_proxy
    sem = asyncio.Semaphore(total_concurrency)

    async def check_with_sem(domain: str, proxy) -> Result:
        async with sem:
            proxy_dict = proxy.to_dict()
            result = await checker.check_single_domain(domain, proxy_dict)
            if result.status in ("taken", "available"):
                pool.report_success(proxy)
            else:
                pool.report_failure(proxy)
            return result

    # Create tasks - distribute domains evenly across proxies
    tasks = []
    proxies = pool.get_healthy_proxies()
    for i, domain in enumerate(domains):
        proxy = proxies[i % len(proxies)]
        tasks.append(check_with_sem(domain, proxy))

    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    # Print sample results
    print(f"\nSample results (first 10):")
    for r in results[:10]:
        status_icon = {"taken": "[T]", "available": "[A]", "error": "[E]", "unknown": "[?]"}.get(r.status, "[?]")
        err = f" ({r.error})" if r.error else ""
        print(f"  {status_icon} {r.domain}{err}")

    # Stats
    stats = checker.stats
    pool_stats = pool.summary()

    print(f"\n{'=' * 60}")
    print("RESULTS")
    print("=" * 60)
    print(f"Total checked:    {stats.total}")
    print(f"Taken:            {stats.taken}")
    print(f"Available:        {stats.available}")
    print(f"Unknown:          {stats.unknown}")
    print(f"Errors:           {stats.errors}")
    print()
    print(f"Tunnels created:  {stats.tunnels_created}")
    print()
    print(f"Bytes sent:       {stats.bytes_sent:,}")
    print(f"Bytes received:   {stats.bytes_received:,}")
    print(f"Total bandwidth:  {stats.bytes_sent + stats.bytes_received:,} bytes")
    print(f"Per query:        {(stats.bytes_sent + stats.bytes_received) / stats.total:.1f} bytes" if stats.total else "N/A")
    print()
    print(f"Time:             {elapsed:.2f}s")
    print(f"Throughput:       {stats.total / elapsed:.1f} domains/sec")

    print(f"\n{'=' * 60}")
    print("PROXY POOL STATS")
    print("=" * 60)
    print(f"Total proxies:    {pool_stats['total']}")
    print(f"Enabled:          {pool_stats['enabled']}")
    print(f"Disabled:         {pool_stats['disabled']}")
    print(f"Success rate:     {pool_stats['overall_success_rate']*100:.1f}%")

    # Scale projections
    print(f"\n{'=' * 60}")
    print("SCALE PROJECTIONS")
    print("=" * 60)
    bytes_per_query = (stats.bytes_sent + stats.bytes_received) / stats.total if stats.total else 0
    total_checks = 580_000_000
    projected_bandwidth_gb = (bytes_per_query * total_checks) / (1024**3)
    throughput = stats.total / elapsed if elapsed > 0 else 0

    print(f"At {bytes_per_query:.0f} bytes/query:")
    print(f"  580M checks = {projected_bandwidth_gb:.1f} GB")
    print(f"  Floxy limit: 250 GB")
    print(f"  Status: {'✓ OK' if projected_bandwidth_gb < 250 else '✗ OVER LIMIT'}")
    print()
    print(f"At {throughput:.0f} domains/sec:")
    time_hours = total_checks / throughput / 3600 if throughput > 0 else 0
    time_days = time_hours / 24
    print(f"  580M checks = {time_hours:.1f} hours ({time_days:.1f} days)")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WHOIS Checker - Iteration 1 & 2")
    parser.add_argument("--test", type=int, default=100, help="Number of domains to test")
    parser.add_argument("--proxies", type=int, default=0, help="Number of proxies (0=single proxy mode)")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrency per proxy")

    args = parser.parse_args()

    if args.proxies > 0:
        # Iteration 2: Multi-proxy mode
        asyncio.run(run_test_multiproxy(args.test, args.proxies, args.concurrency))
    else:
        # Iteration 1: Single proxy mode
        asyncio.run(run_test(args.test, args.concurrency))
