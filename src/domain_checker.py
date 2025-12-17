#!/usr/bin/env python3
"""
Iteration 4-7: Complete Domain Checker

Integrates:
- WHOIS checker (Iteration 1)
- Proxy pool (Iteration 2)
- Database integration (Iteration 4)
- Checkpointing (Iteration 5)
"""

import asyncio
import argparse
import time
from pathlib import Path
from typing import Optional

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP = True
except ImportError:
    UVLOOP = False

from whois_checker import WHOISChecker, Result
from proxy_pool import ProxyPool
from database import DomainDatabase, DomainResult, create_test_database


# Configuration
BATCH_SIZE = 10000
CHECKPOINT_INTERVAL = 100000
CONCURRENCY_PER_PROXY = 1  # OPTIMAL: 1 connection per proxy (proxies are the bottleneck)
MAX_RETRIES = 2  # Retry failed domains

# Bottleneck Analysis Results:
# - Per-proxy limit: ~1-2 concurrent connections per proxy
# - Optimal total concurrency: ~1000 (matches proxy count)
# - Expected throughput: ~3,500/sec at 100% success
# - Projected time for 580M: ~46 hours (~2 days)


class DomainChecker:
    """Complete domain checking system."""

    def __init__(
        self,
        variations_db: Path,
        checks_db: Path,
        proxy_file: Path,
        max_proxies: Optional[int] = None
    ):
        self.db = DomainDatabase(variations_db, checks_db)
        self.pool = ProxyPool(proxy_file, max_proxies)
        self.checker = WHOISChecker()

        # Stats
        self.start_time = None
        self.domains_checked = 0
        self.last_checkpoint = 0

    async def check_domain(self, domain: str, proxy) -> DomainResult:
        """Check single domain and return result."""
        proxy_dict = proxy.to_dict()
        result = await self.checker.check_single_domain(domain, proxy_dict)

        # Track proxy health
        if result.status in ("taken", "available"):
            self.pool.report_success(proxy)
        else:
            self.pool.report_failure(proxy)

        return DomainResult(
            domain=result.domain,
            status=result.status,
            error=result.error
        )

    async def check_batch(self, domains: list[str], concurrency: int = 100) -> list[DomainResult]:
        """Check batch of domains in parallel with retry logic."""
        sem = asyncio.Semaphore(concurrency)
        proxies = self.pool.get_healthy_proxies()

        async def check_with_retry(domain: str, proxy_idx: int) -> DomainResult:
            async with sem:
                # Try with primary proxy
                proxy = proxies[proxy_idx % len(proxies)]
                result = await self.check_domain(domain, proxy)

                # Retry with different proxy if failed
                for retry in range(MAX_RETRIES):
                    if result.status in ("taken", "available"):
                        break
                    # Use different proxy for retry
                    proxy = proxies[(proxy_idx + retry + 1) % len(proxies)]
                    result = await self.check_domain(domain, proxy)

                return result

        # Distribute domains across proxies
        tasks = [check_with_retry(domain, i) for i, domain in enumerate(domains)]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def run(
        self,
        batch_size: int = BATCH_SIZE,
        checkpoint_interval: int = CHECKPOINT_INTERVAL,
        limit: Optional[int] = None,
        resume: bool = False
    ):
        """Run the domain checker."""
        self.start_time = time.perf_counter()

        # Get starting point
        if resume:
            offset, self.domains_checked = self.db.get_checkpoint()
            print(f"Resuming from checkpoint: offset={offset}, checked={self.domains_checked}")
        else:
            offset = 0
            self.domains_checked = 0

        total_domains = self.db.get_total_domains()
        if limit:
            total_domains = min(total_domains, limit)

        print(f"\nTotal domains to check: {total_domains:,}")
        print(f"Using {len(self.pool)} proxies")
        print(f"Batch size: {batch_size}")
        print("-" * 60)

        # Calculate concurrency
        concurrency = len(self.pool) * CONCURRENCY_PER_PROXY

        while self.domains_checked < total_domains:
            # Get next batch
            remaining = total_domains - self.domains_checked
            current_batch_size = min(batch_size, remaining)

            domains = self.db.get_domains_batch(
                batch_size=current_batch_size,
                offset=offset
            )

            if not domains:
                break

            # Check batch
            batch_start = time.perf_counter()
            results = await self.check_batch(domains, concurrency=concurrency)
            batch_time = time.perf_counter() - batch_start

            # Save results
            self.db.save_results(results)

            # Update progress
            self.domains_checked += len(results)
            offset += len(domains)

            # Stats
            elapsed = time.perf_counter() - self.start_time
            throughput = self.domains_checked / elapsed if elapsed > 0 else 0
            batch_throughput = len(results) / batch_time if batch_time > 0 else 0

            # Count results
            taken = sum(1 for r in results if r.status == "taken")
            available = sum(1 for r in results if r.status == "available")
            errors = sum(1 for r in results if r.status == "error")

            print(
                f"Batch: {len(results):,} domains | "
                f"T:{taken} A:{available} E:{errors} | "
                f"{batch_throughput:.0f}/sec | "
                f"Total: {self.domains_checked:,}/{total_domains:,} "
                f"({self.domains_checked/total_domains*100:.1f}%) | "
                f"Overall: {throughput:.0f}/sec"
            )

            # Checkpoint
            if self.domains_checked - self.last_checkpoint >= checkpoint_interval:
                self.db.save_checkpoint(offset, self.domains_checked)
                self.last_checkpoint = self.domains_checked
                print(f"  [Checkpoint saved at {self.domains_checked:,}]")

        # Final stats
        elapsed = time.perf_counter() - self.start_time
        self.print_summary(elapsed)

    def print_summary(self, elapsed: float):
        """Print final summary."""
        print("\n" + "=" * 60)
        print("FINAL RESULTS")
        print("=" * 60)

        stats = self.db.get_stats()
        checker_stats = self.checker.stats
        pool_stats = self.pool.summary()

        print(f"Total checked:    {stats.get('total', 0):,}")
        print(f"Taken:            {stats.get('taken', 0):,}")
        print(f"Available:        {stats.get('available', 0):,}")
        print(f"Unknown:          {stats.get('unknown', 0):,}")
        print(f"Errors:           {stats.get('error', 0):,}")
        print()
        print(f"Time:             {elapsed:.1f}s ({elapsed/60:.1f} min)")
        print(f"Throughput:       {self.domains_checked / elapsed:.0f} domains/sec")
        print()
        print(f"Proxies used:     {pool_stats['total']}")
        print(f"Proxies healthy:  {pool_stats['enabled']}")
        print(f"Proxy success:    {pool_stats['overall_success_rate']*100:.1f}%")
        print()
        print(f"Bytes sent:       {checker_stats.bytes_sent:,}")
        print(f"Bytes received:   {checker_stats.bytes_received:,}")
        print(f"Total bandwidth:  {(checker_stats.bytes_sent + checker_stats.bytes_received) / (1024*1024):.2f} MB")


async def run_iteration4_test():
    """Test database integration with WHOIS checker."""
    print("=" * 60)
    print("ITERATION 4: Database Integration Test")
    print("=" * 60)

    # Create test database
    test_variations = create_test_database(1000)
    test_checks = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_domain_checks.db")

    # Remove old checks
    if test_checks.exists():
        test_checks.unlink()

    # Create checker
    checker = DomainChecker(
        variations_db=test_variations,
        checks_db=test_checks,
        proxy_file=Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt"),
        max_proxies=50
    )

    print(f"\nuvloop: {'enabled' if UVLOOP else 'not available'}")

    # Run with limit
    await checker.run(
        batch_size=500,
        checkpoint_interval=500,
        limit=1000
    )


async def run_iteration5_test():
    """Test checkpoint and resume functionality."""
    print("=" * 60)
    print("ITERATION 5: Checkpoint/Resume Test")
    print("=" * 60)

    # Create test database with 2000 domains
    test_variations = create_test_database(2000)
    test_checks = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_resume_checks.db")

    # Remove old checks
    if test_checks.exists():
        test_checks.unlink()

    proxy_file = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")

    print("\n--- PHASE 1: Check first 1000 domains ---")

    # Create checker
    checker1 = DomainChecker(
        variations_db=test_variations,
        checks_db=test_checks,
        proxy_file=proxy_file,
        max_proxies=50
    )

    # Run with limit of 1000 (simulating interruption at 1000)
    await checker1.run(
        batch_size=500,
        checkpoint_interval=500,
        limit=1000
    )

    # Verify checkpoint
    db = DomainDatabase(test_variations, test_checks)
    checkpoint = db.get_checkpoint()
    stats1 = db.get_stats()
    print(f"\nCheckpoint after Phase 1: offset={checkpoint[0]}, checked={checkpoint[1]}")
    print(f"Results in DB: {stats1}")

    print("\n--- PHASE 2: Resume and check remaining 1000 ---")

    # Create new checker instance (simulating restart)
    checker2 = DomainChecker(
        variations_db=test_variations,
        checks_db=test_checks,
        proxy_file=proxy_file,
        max_proxies=50
    )

    # Resume from checkpoint
    await checker2.run(
        batch_size=500,
        checkpoint_interval=500,
        limit=2000,
        resume=True
    )

    # Final verification
    stats2 = db.get_stats()
    print(f"\nFinal results in DB: {stats2}")

    # Verify no duplicates
    with __import__('sqlite3').connect(test_checks) as conn:
        total_rows = conn.execute("SELECT COUNT(*) FROM domain_checks").fetchone()[0]
        unique_domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM domain_checks").fetchone()[0]

    print(f"\nVerification:")
    print(f"  Total rows: {total_rows}")
    print(f"  Unique domains: {unique_domains}")
    print(f"  No duplicates: {'✓' if total_rows == unique_domains else '✗'}")
    print(f"  Expected ~2000: {'✓' if 1900 <= total_rows <= 2000 else '✗'}")


async def run_iteration6_test():
    """Full scale test with 100K domains (simulated)."""
    print("=" * 60)
    print("ITERATION 6: Full Scale Test (100K domains)")
    print("=" * 60)

    # Create test database with 100K domains
    test_variations = create_test_database(100000)
    test_checks = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_100k_checks.db")

    # Remove old checks
    if test_checks.exists():
        test_checks.unlink()

    proxy_file = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")

    # Create checker with all proxies
    checker = DomainChecker(
        variations_db=test_variations,
        checks_db=test_checks,
        proxy_file=proxy_file,
        max_proxies=None  # Use all available
    )

    print(f"\nuvloop: {'enabled' if UVLOOP else 'not available'}")

    # Run full test
    await checker.run(
        batch_size=10000,
        checkpoint_interval=25000,
        limit=100000
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Domain Checker")
    parser.add_argument("--test", action="store_true", help="Run Iteration 4 test")
    parser.add_argument("--test-resume", action="store_true", help="Run Iteration 5 resume test")
    parser.add_argument("--test-100k", action="store_true", help="Run Iteration 6 100K test")
    parser.add_argument("--limit", type=int, help="Limit number of domains to check")
    parser.add_argument("--proxies", type=int, help="Number of proxies to use")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Batch size")

    args = parser.parse_args()

    if args.test:
        asyncio.run(run_iteration4_test())
    elif args.test_resume:
        asyncio.run(run_iteration5_test())
    elif args.test_100k:
        asyncio.run(run_iteration6_test())
    else:
        print("Available tests:")
        print("  --test        : Iteration 4 - Database integration (1K domains)")
        print("  --test-resume : Iteration 5 - Checkpoint/resume test (2K domains)")
        print("  --test-100k   : Iteration 6 - Full scale test (100K domains)")
        print("  Full production run coming in Iteration 7")
