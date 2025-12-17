#!/usr/bin/env python3
"""
Async batch RDAP checker with concurrency.
Tests throughput with multiple concurrent requests.
"""

import asyncio
import time
import httpx
from pathlib import Path
from itertools import cycle

# RDAP endpoints
RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain/",
}

# Test domains - mix of real and fake
TEST_DOMAINS = [
    # Known taken
    "google.com", "amazon.com", "facebook.com", "apple.com", "microsoft.com",
    "github.com", "netflix.com", "twitter.com", "linkedin.com", "instagram.com",
    # Likely available (random strings)
    "xyzabc123456.com", "qwerty987654.com", "foobar999888.com",
    "testdomain12345.net", "randomllc99999.net", "bizname777666.org",
    "acmecorp123456.com", "atlbusiness8888.com", "gacompany7777.net",
    "uniquellc999999.com",
]

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxies(limit: int = 50) -> list[str]:
    """Load proxies from file."""
    proxies = []
    with open(PROXY_FILE) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if line:
                proxies.append(f"http://{line}")
    return proxies


async def check_domain(
    client: httpx.AsyncClient,
    domain: str,
    semaphore: asyncio.Semaphore
) -> dict:
    """Check domain availability with semaphore for concurrency control."""
    async with semaphore:
        tld = domain.split(".")[-1]
        base_url = RDAP_ENDPOINTS.get(tld)

        if not base_url:
            return {"domain": domain, "status": "error", "detail": f"Unknown TLD"}

        url = f"{base_url}{domain}"

        try:
            response = await client.get(url, timeout=15.0)

            if response.status_code == 200:
                return {"domain": domain, "status": "taken", "detail": "registered"}
            elif response.status_code == 404:
                return {"domain": domain, "status": "available", "detail": "not found"}
            else:
                return {"domain": domain, "status": "error", "detail": f"HTTP {response.status_code}"}

        except httpx.TimeoutException:
            return {"domain": domain, "status": "error", "detail": "timeout"}
        except Exception as e:
            return {"domain": domain, "status": "error", "detail": str(type(e).__name__)}


async def batch_check(domains: list[str], proxies: list[str], concurrency: int = 10):
    """
    Check multiple domains concurrently with proxy rotation.
    """
    print(f"\nChecking {len(domains)} domains with {concurrency} concurrent requests...")
    print(f"Using {len(proxies)} proxies")
    print("-" * 60)

    semaphore = asyncio.Semaphore(concurrency)
    proxy_cycle = cycle(proxies)

    start_time = time.time()
    results = []

    # Create tasks with rotating proxies
    # Group domains by proxy to reuse connections
    proxy_groups = {}
    for domain in domains:
        proxy = next(proxy_cycle)
        if proxy not in proxy_groups:
            proxy_groups[proxy] = []
        proxy_groups[proxy].append(domain)

    # Process each proxy group
    async def process_proxy_group(proxy: str, group_domains: list[str]):
        group_results = []
        async with httpx.AsyncClient(proxy=proxy) as client:
            tasks = [check_domain(client, d, semaphore) for d in group_domains]
            group_results = await asyncio.gather(*tasks)
        return group_results

    # Run all proxy groups concurrently
    all_tasks = [
        process_proxy_group(proxy, group_domains)
        for proxy, group_domains in proxy_groups.items()
    ]

    all_results = await asyncio.gather(*all_tasks)

    # Flatten results
    for group_result in all_results:
        results.extend(group_result)

    elapsed = time.time() - start_time

    # Print results
    for r in sorted(results, key=lambda x: x["domain"]):
        icon = {"taken": "[T]", "available": "[A]", "error": "[E]"}.get(r["status"], "[?]")
        print(f"{icon} {r['domain']:30} {r['detail']}")

    # Summary
    print("-" * 60)
    taken = sum(1 for r in results if r["status"] == "taken")
    available = sum(1 for r in results if r["status"] == "available")
    errors = sum(1 for r in results if r["status"] == "error")

    print(f"\nResults: {taken} taken, {available} available, {errors} errors")
    print(f"Time: {elapsed:.2f}s")
    print(f"Throughput: {len(domains)/elapsed:.1f} domains/sec")

    return results


async def main():
    print("=" * 60)
    print("Async Batch RDAP Checker Test")
    print("=" * 60)

    proxies = load_proxies(limit=20)
    print(f"Loaded {len(proxies)} proxies")

    # Test with different concurrency levels
    for concurrency in [5, 10, 20]:
        print(f"\n{'='*60}")
        print(f"Testing with concurrency={concurrency}")
        await batch_check(TEST_DOMAINS, proxies, concurrency=concurrency)


if __name__ == "__main__":
    asyncio.run(main())
