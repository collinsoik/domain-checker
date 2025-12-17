#!/usr/bin/env python3
"""
Optimized RDAP checker - minimal bandwidth.
Uses streaming to avoid downloading response body.
"""

import asyncio
import httpx
from pathlib import Path

RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain/",
}

TEST_DOMAINS = [
    "google.com",           # taken
    "amazon.com",           # taken
    "xyzabc123456.com",     # available
    "microsoft.com",        # taken
    "randomllc99999.net",   # available
    "github.com",           # taken
    "foobar777888.org",     # available
]

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxy() -> str:
    with open(PROXY_FILE) as f:
        return f"http://{f.readline().strip()}"


async def check_optimized(client: httpx.AsyncClient, domain: str) -> dict:
    """
    Optimized check - uses streaming to avoid downloading body.
    Only reads enough to get status code, then closes.
    """
    tld = domain.split(".")[-1]
    url = f"{RDAP_ENDPOINTS[tld]}{domain}"

    # Estimate request size
    request_size = 170  # approximate

    try:
        # Use stream context - this sends request and gets headers WITHOUT downloading body
        async with client.stream("GET", url, timeout=15.0) as response:
            # We have the status code - that's all we need!
            status_code = response.status_code

            # Calculate header size received
            headers_str = "\r\n".join(f"{k}: {v}" for k, v in response.headers.items())
            response_headers_size = len(headers_str) + 20  # status line estimate

            # DO NOT read body - just close the stream
            # response.content would download body - we skip it

            if status_code == 200:
                status = "taken"
            elif status_code == 404:
                status = "available"
            else:
                status = "error"

            return {
                "domain": domain,
                "status": status,
                "status_code": status_code,
                "request_bytes": request_size,
                "response_bytes": response_headers_size,  # Headers only!
                "total_bytes": request_size + response_headers_size,
                "body_downloaded": False,
            }

    except Exception as e:
        return {
            "domain": domain,
            "status": "error",
            "error": str(e),
            "request_bytes": request_size,
            "response_bytes": 0,
            "total_bytes": request_size,
            "body_downloaded": False,
        }


async def check_full_body(client: httpx.AsyncClient, domain: str) -> dict:
    """Original method - downloads full body for comparison."""
    tld = domain.split(".")[-1]
    url = f"{RDAP_ENDPOINTS[tld]}{domain}"
    request_size = 170

    try:
        response = await client.get(url, timeout=15.0)
        body = response.content
        headers_str = "\r\n".join(f"{k}: {v}" for k, v in response.headers.items())
        response_size = len(headers_str) + len(body) + 20

        status = "taken" if response.status_code == 200 else "available" if response.status_code == 404 else "error"

        return {
            "domain": domain,
            "status": status,
            "request_bytes": request_size,
            "response_bytes": response_size,
            "total_bytes": request_size + response_size,
            "body_downloaded": True,
        }
    except Exception as e:
        return {"domain": domain, "status": "error", "total_bytes": request_size, "body_downloaded": True}


async def main():
    print("=" * 70)
    print("BANDWIDTH COMPARISON: Full Body vs Streaming (Headers Only)")
    print("=" * 70)

    proxy = load_proxy()

    # Test both methods
    full_results = []
    stream_results = []

    async with httpx.AsyncClient(proxy=proxy) as client:
        print("\n[1] Testing FULL BODY download (current approach)...")
        for domain in TEST_DOMAINS:
            result = await check_full_body(client, domain)
            full_results.append(result)

        print("[2] Testing STREAMING (headers only - optimized)...")
        for domain in TEST_DOMAINS:
            result = await check_optimized(client, domain)
            stream_results.append(result)

    # Compare results
    print(f"\n{'Domain':<25} {'Status':<10} {'Full Body':>12} {'Stream':>12} {'Savings':>10}")
    print("-" * 70)

    total_full = 0
    total_stream = 0

    for full, stream in zip(full_results, stream_results):
        savings = full['total_bytes'] - stream['total_bytes']
        savings_pct = (savings / full['total_bytes'] * 100) if full['total_bytes'] > 0 else 0
        print(f"{full['domain']:<25} {full['status']:<10} {full['total_bytes']:>10} B {stream['total_bytes']:>10} B {savings_pct:>8.1f}%")
        total_full += full['total_bytes']
        total_stream += stream['total_bytes']

    print("-" * 70)
    print(f"{'TOTAL':<25} {'':<10} {total_full:>10} B {total_stream:>10} B {(total_full-total_stream)/total_full*100:>8.1f}%")

    # Scale projections
    print("\n" + "=" * 70)
    print("SCALE PROJECTIONS (580M checks, 10% taken)")
    print("=" * 70)

    checks = 580_000_000

    # Calculate averages per status
    full_taken = [r for r in full_results if r['status'] == 'taken']
    full_avail = [r for r in full_results if r['status'] == 'available']
    stream_taken = [r for r in stream_results if r['status'] == 'taken']
    stream_avail = [r for r in stream_results if r['status'] == 'available']

    avg_full_taken = sum(r['total_bytes'] for r in full_taken) / len(full_taken) if full_taken else 0
    avg_full_avail = sum(r['total_bytes'] for r in full_avail) / len(full_avail) if full_avail else 0
    avg_stream_taken = sum(r['total_bytes'] for r in stream_taken) / len(stream_taken) if stream_taken else 0
    avg_stream_avail = sum(r['total_bytes'] for r in stream_avail) / len(stream_avail) if stream_avail else 0

    # 10% taken, 90% available
    full_bandwidth = (checks * 0.1 * avg_full_taken + checks * 0.9 * avg_full_avail) / (1024**3)
    stream_bandwidth = (checks * 0.1 * avg_stream_taken + checks * 0.9 * avg_stream_avail) / (1024**3)

    print(f"\nFull Body Method:    {full_bandwidth:>8.1f} GB")
    print(f"Streaming Method:    {stream_bandwidth:>8.1f} GB")
    print(f"Bandwidth Saved:     {full_bandwidth - stream_bandwidth:>8.1f} GB")
    print(f"Floxy Limit:         {250:>8} GB")
    print(f"\nFull body status:    {'OVER LIMIT!' if full_bandwidth > 250 else 'OK'}")
    print(f"Streaming status:    {'OVER LIMIT!' if stream_bandwidth > 250 else 'OK'}")

    # Additional scenarios
    print("\n" + "=" * 70)
    print("BANDWIDTH BY TAKEN RATIO")
    print("=" * 70)
    print(f"\n{'% Taken':<10} {'Full Body':>15} {'Streaming':>15} {'Under 250GB?':>15}")
    print("-" * 55)

    for pct_taken in [0.05, 0.10, 0.20, 0.30, 0.50]:
        pct_avail = 1 - pct_taken
        full_gb = (checks * pct_taken * avg_full_taken + checks * pct_avail * avg_full_avail) / (1024**3)
        stream_gb = (checks * pct_taken * avg_stream_taken + checks * pct_avail * avg_stream_avail) / (1024**3)
        status = "YES" if stream_gb < 250 else "NO"
        print(f"{pct_taken*100:>5.0f}%     {full_gb:>12.1f} GB {stream_gb:>12.1f} GB {status:>15}")


if __name__ == "__main__":
    asyncio.run(main())
