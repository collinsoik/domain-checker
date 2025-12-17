#!/usr/bin/env python3
"""
Test WHOIS protocol as alternative/complement to RDAP.
WHOIS uses port 43, plain text protocol.
"""

import asyncio
import time
import socket
from pathlib import Path
from statistics import mean, median

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# WHOIS servers by TLD
WHOIS_SERVERS = {
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "org": "whois.pir.org",
    "io": "whois.nic.io",
    "co": "whois.nic.co",
}

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def parse_proxy(proxy_line: str) -> tuple[str, int, str, str]:
    """Parse proxy format: user:pass@ip:port"""
    # Format: user:pass@ip:port
    auth, hostport = proxy_line.split("@")
    user, passwd = auth.split(":")
    host, port = hostport.split(":")
    return host, int(port), user, passwd


def load_proxies(limit: int = 10) -> list[str]:
    proxies = []
    with open(PROXY_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                proxies.append(line)
                if len(proxies) >= limit:
                    break
    return proxies


async def whois_query_direct(domain: str, timeout: float = 10.0) -> dict:
    """
    Direct WHOIS query (no proxy) for baseline latency.
    """
    tld = domain.split(".")[-1]
    server = WHOIS_SERVERS.get(tld, "whois.verisign-grs.com")

    start = time.perf_counter()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(server, 43),
            timeout=timeout
        )

        # Send query
        query = f"{domain}\r\n"
        writer.write(query.encode())
        await writer.drain()

        # Read response (just enough to determine status)
        response = await asyncio.wait_for(
            reader.read(2048),  # Read first 2KB only
            timeout=timeout
        )

        writer.close()
        await writer.wait_closed()

        elapsed = (time.perf_counter() - start) * 1000
        response_text = response.decode('utf-8', errors='ignore')

        # Determine if domain is taken or available
        # Verisign returns "No match for" if available
        if "No match for" in response_text or "NOT FOUND" in response_text.upper():
            status = "available"
        elif "Domain Name:" in response_text or "domain name:" in response_text.lower():
            status = "taken"
        else:
            status = "unknown"

        return {
            "domain": domain,
            "status": status,
            "latency_ms": elapsed,
            "response_size": len(response),
            "success": True,
        }

    except asyncio.TimeoutError:
        return {"domain": domain, "status": "timeout", "success": False, "latency_ms": timeout * 1000}
    except Exception as e:
        return {"domain": domain, "status": "error", "error": str(e), "success": False, "latency_ms": 0}


async def test_whois_latency():
    """Test WHOIS latency with various domains."""
    print("=" * 70)
    print("WHOIS LATENCY TEST (Direct Connection)")
    print("=" * 70)

    # Test domains
    domains = [
        "google.com",        # taken
        "amazon.com",        # taken
        "xyztest123456.com", # available
        "microsoft.com",     # taken
        "randomllc99999.com", # available
    ]

    print(f"\n{'Domain':<25} {'Status':<12} {'Latency':>10} {'Size':>10}")
    print("-" * 60)

    results = []
    for domain in domains:
        result = await whois_query_direct(domain)
        results.append(result)

        if result['success']:
            print(f"{domain:<25} {result['status']:<12} {result['latency_ms']:>8.0f}ms {result.get('response_size', 0):>8}B")
        else:
            print(f"{domain:<25} {result['status']:<12} FAILED")

        await asyncio.sleep(0.5)  # Small delay between queries

    # Summary
    successful = [r for r in results if r['success']]
    if successful:
        latencies = [r['latency_ms'] for r in successful]
        print(f"\n{'Summary':<25}")
        print(f"  Avg latency: {mean(latencies):.0f}ms")
        print(f"  Median: {median(latencies):.0f}ms")
        print(f"  Min: {min(latencies):.0f}ms")
        print(f"  Max: {max(latencies):.0f}ms")

    return results


async def test_whois_concurrent(num_domains: int = 20, concurrency: int = 10):
    """Test concurrent WHOIS queries."""
    print(f"\n{'=' * 70}")
    print(f"WHOIS CONCURRENT TEST ({num_domains} domains, {concurrency} concurrent)")
    print("=" * 70)

    # Generate test domains
    domains = []
    real = ["google.com", "amazon.com", "microsoft.com"]
    for i in range(num_domains):
        if i % 5 == 0:
            domains.append(real[i % len(real)])
        else:
            domains.append(f"testbiz{i:06d}.com")

    semaphore = asyncio.Semaphore(concurrency)

    async def limited_query(domain: str):
        async with semaphore:
            return await whois_query_direct(domain, timeout=15.0)

    start = time.perf_counter()
    tasks = [limited_query(d) for d in domains]
    results = await asyncio.gather(*tasks)
    elapsed = (time.perf_counter() - start) * 1000

    successful = [r for r in results if r['success']]
    taken = len([r for r in successful if r['status'] == 'taken'])
    available = len([r for r in successful if r['status'] == 'available'])
    errors = len([r for r in results if not r['success']])

    throughput = len(domains) / (elapsed / 1000)

    print(f"\nResults:")
    print(f"  Total: {len(domains)} domains in {elapsed:.0f}ms")
    print(f"  Throughput: {throughput:.1f} domains/sec")
    print(f"  Taken: {taken}, Available: {available}, Errors: {errors}")

    if successful:
        latencies = [r['latency_ms'] for r in successful]
        print(f"  Avg latency: {mean(latencies):.0f}ms")

    return throughput


async def main():
    # Test 1: Basic latency
    await test_whois_latency()

    # Test 2: Concurrent
    throughput = await test_whois_concurrent(num_domains=30, concurrency=15)

    # Summary comparison with RDAP
    print("\n" + "=" * 70)
    print("WHOIS vs RDAP COMPARISON (from previous tests)")
    print("=" * 70)

    print("""
    Protocol    Single Latency    Concurrent Throughput
    --------    --------------    ---------------------
    RDAP        ~175ms            ~180/sec (with proxies)
    WHOIS       ~???ms            ~{throughput:.0f}/sec (direct)

    Note: WHOIS test was DIRECT (no proxy).
    WHOIS through proxy would need SOCKS5 support.
    """.format(throughput=throughput))


if __name__ == "__main__":
    asyncio.run(main())
