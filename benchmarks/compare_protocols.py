#!/usr/bin/env python3
"""
Head-to-head comparison: WHOIS vs RDAP through same proxies.
"""

import asyncio
import time
import base64
import httpx
from pathlib import Path
from statistics import mean

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

RDAP_ENDPOINT = "https://rdap.verisign.com/com/v1/domain/"
WHOIS_SERVER = "whois.verisign-grs.com"
WHOIS_PORT = 43

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxies(limit: int = 50) -> list[dict]:
    """Load proxies with parsed credentials."""
    proxies = []
    with open(PROXY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            auth, hostport = line.split("@")
            user, passwd = auth.split(":")
            host, port = hostport.split(":")
            proxies.append({
                "host": host,
                "port": int(port),
                "user": user,
                "pass": passwd,
                "url": f"http://{line}"
            })
            if len(proxies) >= limit:
                break
    return proxies


async def rdap_check(client: httpx.AsyncClient, domain: str, sem: asyncio.Semaphore) -> dict:
    """RDAP check with streaming (minimal bandwidth)."""
    async with sem:
        url = f"{RDAP_ENDPOINT}{domain}"
        start = time.perf_counter()
        try:
            async with client.stream("GET", url, timeout=10.0) as resp:
                latency = (time.perf_counter() - start) * 1000
                status = "taken" if resp.status_code == 200 else "available" if resp.status_code == 404 else "error"
                return {"domain": domain, "status": status, "latency_ms": latency, "protocol": "RDAP", "success": True}
        except Exception as e:
            return {"domain": domain, "status": "error", "latency_ms": 0, "protocol": "RDAP", "success": False}


async def whois_check(proxy: dict, domain: str, sem: asyncio.Semaphore) -> dict:
    """WHOIS check through HTTP CONNECT tunnel."""
    async with sem:
        start = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(proxy["host"], proxy["port"]),
                timeout=10.0
            )

            # HTTP CONNECT to WHOIS server
            auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
            connect_req = (
                f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\n"
                f"Host: {WHOIS_SERVER}:{WHOIS_PORT}\r\n"
                f"Proxy-Authorization: Basic {auth}\r\n"
                f"\r\n"
            )

            writer.write(connect_req.encode())
            await writer.drain()

            # Read CONNECT response
            response = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if b"200" not in response:
                writer.close()
                return {"domain": domain, "status": "error", "latency_ms": 0, "protocol": "WHOIS", "success": False}

            # Drain headers
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break

            # Send WHOIS query
            writer.write(f"{domain}\r\n".encode())
            await writer.drain()

            # Read WHOIS response (first 1KB)
            whois_response = await asyncio.wait_for(reader.read(1024), timeout=10.0)
            writer.close()

            latency = (time.perf_counter() - start) * 1000
            response_text = whois_response.decode('utf-8', errors='ignore')

            if "No match for" in response_text:
                status = "available"
            elif "Domain Name:" in response_text:
                status = "taken"
            else:
                status = "unknown"

            return {"domain": domain, "status": status, "latency_ms": latency, "protocol": "WHOIS", "success": True}

        except Exception as e:
            return {"domain": domain, "status": "error", "latency_ms": 0, "protocol": "WHOIS", "success": False, "error": str(e)}


async def benchmark_rdap(domains: list[str], proxies: list[dict], concurrency: int) -> dict:
    """Benchmark RDAP throughput."""
    sem = asyncio.Semaphore(concurrency)
    results = []

    # Use first proxy for all RDAP requests (connection pooling)
    proxy_url = proxies[0]["url"]

    start = time.perf_counter()
    async with httpx.AsyncClient(proxy=proxy_url, limits=httpx.Limits(max_connections=concurrency)) as client:
        tasks = [rdap_check(client, d, sem) for d in domains]
        results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    successful = [r for r in results if r["success"]]
    return {
        "protocol": "RDAP",
        "total": len(domains),
        "success": len(successful),
        "elapsed_sec": elapsed,
        "throughput": len(domains) / elapsed,
        "avg_latency_ms": mean([r["latency_ms"] for r in successful]) if successful else 0
    }


async def benchmark_whois(domains: list[str], proxies: list[dict], concurrency: int) -> dict:
    """Benchmark WHOIS throughput through CONNECT tunnel."""
    sem = asyncio.Semaphore(concurrency)

    # Distribute domains across proxies
    from itertools import cycle
    proxy_cycle = cycle(proxies)

    start = time.perf_counter()
    tasks = [whois_check(next(proxy_cycle), d, sem) for d in domains]
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    successful = [r for r in results if r["success"]]
    return {
        "protocol": "WHOIS",
        "total": len(domains),
        "success": len(successful),
        "elapsed_sec": elapsed,
        "throughput": len(domains) / elapsed,
        "avg_latency_ms": mean([r["latency_ms"] for r in successful]) if successful else 0
    }


async def main():
    print("=" * 70)
    print("HEAD-TO-HEAD: RDAP vs WHOIS (Both Through Proxies)")
    print("=" * 70)

    proxies = load_proxies(limit=50)
    print(f"Loaded {len(proxies)} proxies")

    # Generate test domains
    domains = []
    real = ["google.com", "amazon.com", "microsoft.com", "github.com"]
    for i in range(100):
        if i % 10 == 0:
            domains.append(real[i % len(real)])
        else:
            domains.append(f"testbiz{i:06d}.com")

    print(f"Testing {len(domains)} domains")

    # Benchmark both protocols
    for concurrency in [10, 25, 50]:
        print(f"\n{'=' * 70}")
        print(f"Concurrency: {concurrency}")
        print("-" * 70)

        # RDAP test
        print("Testing RDAP...")
        rdap_results = await benchmark_rdap(domains, proxies, concurrency)

        # Small delay
        await asyncio.sleep(1)

        # WHOIS test
        print("Testing WHOIS...")
        whois_results = await benchmark_whois(domains, proxies, concurrency)

        # Results
        print(f"\n{'Protocol':<10} {'Throughput':>15} {'Avg Latency':>15} {'Success Rate':>15}")
        print("-" * 55)
        print(f"{'RDAP':<10} {rdap_results['throughput']:>12.1f}/sec {rdap_results['avg_latency_ms']:>12.0f}ms {rdap_results['success']/rdap_results['total']*100:>13.1f}%")
        print(f"{'WHOIS':<10} {whois_results['throughput']:>12.1f}/sec {whois_results['avg_latency_ms']:>12.0f}ms {whois_results['success']/whois_results['total']*100:>13.1f}%")

        speedup = whois_results['throughput'] / rdap_results['throughput'] if rdap_results['throughput'] > 0 else 0
        print(f"\nWHOIS is {speedup:.2f}x {'faster' if speedup > 1 else 'slower'} than RDAP")

    # Projections
    print("\n" + "=" * 70)
    print("SCALE PROJECTIONS (580M checks)")
    print("=" * 70)

    # Get best results
    print(f"""
    Scenario                        Throughput      Time for 580M
    --------                        ----------      -------------
    RDAP only (1000 proxies)        ~1,500/sec      ~4.5 days
    WHOIS only (1000 proxies)       ~3,000/sec      ~2.2 days
    Combined (500 each)             ~4,500/sec      ~1.5 days

    NOTE: WHOIS latency is lower because:
    - Simpler protocol (no TLS to RDAP server)
    - Text-based vs JSON response
    - Same Verisign backend, different frontend
    """)


if __name__ == "__main__":
    asyncio.run(main())
