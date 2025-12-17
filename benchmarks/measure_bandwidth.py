#!/usr/bin/env python3
"""
Measure actual bandwidth usage of RDAP requests.
Captures request/response sizes to verify scale calculations.
"""

import asyncio
import httpx
from pathlib import Path

RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain/",
}

# Mix of taken and available domains
TEST_DOMAINS = [
    "google.com",           # taken - large response
    "amazon.com",           # taken - large response
    "xyzabc123456.com",     # available - small 404
    "microsoft.com",        # taken - large response
    "randomllc99999.net",   # available - small 404
    "github.com",           # taken
    "foobar777888.org",     # available
]

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxy() -> str:
    with open(PROXY_FILE) as f:
        line = f.readline().strip()
        return f"http://{line}"


async def measure_request(client: httpx.AsyncClient, domain: str) -> dict:
    """Measure actual bytes sent and received for a single RDAP request."""
    tld = domain.split(".")[-1]
    base_url = RDAP_ENDPOINTS.get(tld)
    url = f"{base_url}{domain}"

    # Build the request to measure what we're sending
    request = client.build_request("GET", url)

    # Calculate request size (method + URL + headers)
    request_line = f"GET {request.url.raw_path.decode()} HTTP/1.1\r\n"
    headers_str = "\r\n".join(f"{k}: {v}" for k, v in request.headers.items())
    request_size = len(request_line) + len(headers_str) + 4  # +4 for \r\n\r\n

    try:
        response = await client.get(url, timeout=15.0)

        # Response size = status line + headers + body
        status_line = f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
        resp_headers_str = "\r\n".join(f"{k}: {v}" for k, v in response.headers.items())
        body = response.content

        response_headers_size = len(status_line) + len(resp_headers_str) + 4
        response_body_size = len(body)
        response_total = response_headers_size + response_body_size

        status = "taken" if response.status_code == 200 else "available" if response.status_code == 404 else "error"

        return {
            "domain": domain,
            "status": status,
            "request_bytes": request_size,
            "response_headers_bytes": response_headers_size,
            "response_body_bytes": response_body_size,
            "response_total_bytes": response_total,
            "total_bytes": request_size + response_total,
        }

    except Exception as e:
        return {
            "domain": domain,
            "status": "error",
            "error": str(e),
            "request_bytes": request_size,
            "response_headers_bytes": 0,
            "response_body_bytes": 0,
            "response_total_bytes": 0,
            "total_bytes": request_size,
        }


async def main():
    print("=" * 70)
    print("RDAP Bandwidth Measurement")
    print("=" * 70)

    proxy = load_proxy()
    print(f"Using proxy: {proxy[:40]}...")

    results = []

    async with httpx.AsyncClient(proxy=proxy) as client:
        for domain in TEST_DOMAINS:
            result = await measure_request(client, domain)
            results.append(result)

    # Print detailed results
    print(f"\n{'Domain':<25} {'Status':<10} {'Req':>8} {'Resp Hdr':>10} {'Resp Body':>12} {'Total':>10}")
    print("-" * 70)

    for r in results:
        print(f"{r['domain']:<25} {r['status']:<10} {r['request_bytes']:>8} {r['response_headers_bytes']:>10} {r['response_body_bytes']:>12} {r['total_bytes']:>10}")

    # Calculate averages
    taken = [r for r in results if r['status'] == 'taken']
    available = [r for r in results if r['status'] == 'available']

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if taken:
        avg_taken_req = sum(r['request_bytes'] for r in taken) / len(taken)
        avg_taken_resp = sum(r['response_total_bytes'] for r in taken) / len(taken)
        avg_taken_body = sum(r['response_body_bytes'] for r in taken) / len(taken)
        avg_taken_total = sum(r['total_bytes'] for r in taken) / len(taken)
        print(f"\nTAKEN domains ({len(taken)} samples):")
        print(f"  Avg request:        {avg_taken_req:,.0f} bytes")
        print(f"  Avg response total: {avg_taken_resp:,.0f} bytes")
        print(f"  Avg response body:  {avg_taken_body:,.0f} bytes  <-- THIS IS THE PROBLEM")
        print(f"  Avg total transfer: {avg_taken_total:,.0f} bytes")

    if available:
        avg_avail_req = sum(r['request_bytes'] for r in available) / len(available)
        avg_avail_resp = sum(r['response_total_bytes'] for r in available) / len(available)
        avg_avail_body = sum(r['response_body_bytes'] for r in available) / len(available)
        avg_avail_total = sum(r['total_bytes'] for r in available) / len(available)
        print(f"\nAVAILABLE domains ({len(available)} samples):")
        print(f"  Avg request:        {avg_avail_req:,.0f} bytes")
        print(f"  Avg response total: {avg_avail_resp:,.0f} bytes")
        print(f"  Avg response body:  {avg_avail_body:,.0f} bytes")
        print(f"  Avg total transfer: {avg_avail_total:,.0f} bytes")

    # Scale projections
    print("\n" + "=" * 70)
    print("SCALE PROJECTIONS (580M checks)")
    print("=" * 70)

    # Assume 30% taken, 70% available (conservative)
    total_checks = 580_000_000
    pct_taken = 0.10  # Most LLC domains won't be taken
    pct_available = 0.90

    if taken and available:
        avg_taken = sum(r['total_bytes'] for r in taken) / len(taken)
        avg_avail = sum(r['total_bytes'] for r in available) / len(available)

        estimated_bytes = (total_checks * pct_taken * avg_taken) + (total_checks * pct_available * avg_avail)
        estimated_gb = estimated_bytes / (1024**3)

        print(f"\nAssuming {pct_taken*100:.0f}% taken, {pct_available*100:.0f}% available:")
        print(f"  Estimated total bandwidth: {estimated_gb:,.1f} GB")
        print(f"  Floxy limit: 250 GB")
        print(f"  Status: {'OK' if estimated_gb < 250 else 'OVER LIMIT!'}")

        # What if we only read headers (HEAD request or stream)?
        avg_taken_headers_only = sum(r['request_bytes'] + r['response_headers_bytes'] for r in taken) / len(taken)
        avg_avail_headers_only = sum(r['request_bytes'] + r['response_headers_bytes'] for r in available) / len(available)

        optimized_bytes = (total_checks * pct_taken * avg_taken_headers_only) + (total_checks * pct_available * avg_avail_headers_only)
        optimized_gb = optimized_bytes / (1024**3)

        print(f"\nIF we skip response body (headers only):")
        print(f"  Estimated bandwidth: {optimized_gb:,.1f} GB")
        print(f"  Savings: {estimated_gb - optimized_gb:,.1f} GB ({(1 - optimized_gb/estimated_gb)*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
