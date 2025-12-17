#!/usr/bin/env python3
"""
Minimal RDAP domain checker test.
Tests async RDAP lookups with proxy rotation.
"""

import asyncio
import httpx
from pathlib import Path

# RDAP endpoints for common TLDs
RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain/",
    "io": "https://rdap.nic.io/domain/",
    "co": "https://rdap.nic.co/domain/",
}

# Test domains (mix of taken and likely available)
TEST_DOMAINS = [
    "google.com",        # taken
    "amazon.com",        # taken
    "xyzabc123456.com",  # likely available
    "github.io",         # taken
    "randomtest99999.net",  # likely available
]

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxies(limit: int = 10) -> list[str]:
    """Load proxies from file. Format: user:pass@ip:port"""
    proxies = []
    with open(PROXY_FILE) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if line:
                # Format is user:pass@ip:port, convert to http://user:pass@ip:port
                proxies.append(f"http://{line}")
    return proxies


async def check_domain(client: httpx.AsyncClient, domain: str, proxy: str) -> dict:
    """
    Check if a domain is taken or available via RDAP.
    Returns: {"domain": str, "status": "taken"|"available"|"error", "detail": str}
    """
    tld = domain.split(".")[-1]
    base_url = RDAP_ENDPOINTS.get(tld)

    if not base_url:
        return {"domain": domain, "status": "error", "detail": f"Unknown TLD: {tld}"}

    url = f"{base_url}{domain}"

    try:
        # Use stream to minimize bandwidth - we only need status code
        response = await client.get(url, timeout=10.0)

        if response.status_code == 200:
            return {"domain": domain, "status": "taken", "detail": "Domain registered"}
        elif response.status_code == 404:
            return {"domain": domain, "status": "available", "detail": "Domain not found"}
        else:
            return {"domain": domain, "status": "error", "detail": f"HTTP {response.status_code}"}

    except httpx.TimeoutException:
        return {"domain": domain, "status": "error", "detail": "Timeout"}
    except httpx.ProxyError as e:
        return {"domain": domain, "status": "error", "detail": f"Proxy error: {e}"}
    except Exception as e:
        return {"domain": domain, "status": "error", "detail": str(e)}


async def main():
    print("=" * 50)
    print("Minimal RDAP Domain Checker Test")
    print("=" * 50)

    # Load proxies
    proxies = load_proxies(limit=5)
    print(f"\nLoaded {len(proxies)} proxies")
    print(f"First proxy: {proxies[0][:30]}...")

    # Test domains
    print(f"\nTesting {len(TEST_DOMAINS)} domains...")
    print("-" * 50)

    results = []

    # Test each domain with rotating proxy
    for i, domain in enumerate(TEST_DOMAINS):
        proxy = proxies[i % len(proxies)]

        # Create client with proxy for this request
        async with httpx.AsyncClient(proxy=proxy) as client:
            result = await check_domain(client, domain, proxy)
            results.append(result)

            status_emoji = {
                "taken": "[TAKEN]",
                "available": "[AVAIL]",
                "error": "[ERROR]"
            }.get(result["status"], "[?]")

            print(f"{status_emoji} {domain:25} - {result['detail']}")

    # Summary
    print("-" * 50)
    taken = sum(1 for r in results if r["status"] == "taken")
    available = sum(1 for r in results if r["status"] == "available")
    errors = sum(1 for r in results if r["status"] == "error")

    print(f"\nSummary: {taken} taken, {available} available, {errors} errors")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
