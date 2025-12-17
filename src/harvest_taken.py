#!/usr/bin/env python3
"""
Harvest Taken Domains Utility

Checks unique .com domains (one per LLC) from domain_variations.duckdb
and collects taken domains into a new database until the target count is reached.

Usage:
    python harvest_taken.py --target 100000
    python harvest_taken.py --target 1000 --output data/test_taken.duckdb
"""

import asyncio
import argparse
import os
import time
import duckdb
from pathlib import Path
from typing import Optional

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP = True
except ImportError:
    UVLOOP = False

from whois_checker import WHOISChecker
from proxy_pool import ProxyPool
from database import VARIATIONS_DUCKDB, DomainResult

# Configuration
BATCH_SIZE = 10000
CONCURRENCY_PER_PROXY = 1
MAX_RETRIES = 2

# Default paths
_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_OUTPUT = _DEFAULT_DATA_DIR / "taken_domains.duckdb"


class TakenHarvester:
    """Harvests taken .com domains (one per LLC) until target count reached."""

    def __init__(
        self,
        source_db: Path,
        output_db: Path,
        proxy_file: Path,
        max_proxies: Optional[int] = None
    ):
        self.source_db = source_db
        self.output_db = output_db
        self.pool = ProxyPool(proxy_file, max_proxies)
        self.checker = WHOISChecker()

        # Connections
        self._source_conn = None
        self._output_conn = None

        # Stats
        self.start_time = None
        self.domains_checked = 0
        self.taken_count = 0
        self.offset = 0

        # Initialize output database
        self._init_output_db()

    def _get_source_conn(self):
        """Get read-only connection to source database."""
        if self._source_conn is None:
            self._source_conn = duckdb.connect(str(self.source_db), read_only=True)
        return self._source_conn

    def _get_output_conn(self):
        """Get connection to output database."""
        if self._output_conn is None:
            self._output_conn = duckdb.connect(str(self.output_db))
        return self._output_conn

    def _init_output_db(self):
        """Initialize output database with required tables."""
        conn = self._get_output_conn()

        # Create domain_variations table (same name for compatibility)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS domain_variations (
                domain VARCHAR PRIMARY KEY
            )
        """)

        # Create checkpoint table for resume
        conn.execute("""
            CREATE TABLE IF NOT EXISTS harvest_checkpoint (
                id INTEGER PRIMARY KEY,
                source_offset BIGINT,
                domains_checked BIGINT,
                taken_count BIGINT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def get_unique_com_domains(self, batch_size: int, offset: int) -> list[str]:
        """Get unique .com domains (one per LLC)."""
        conn = self._get_source_conn()
        results = conn.execute("""
            SELECT domain FROM (
                SELECT domain, llc_id,
                       ROW_NUMBER() OVER (PARTITION BY llc_id ORDER BY id) as rn
                FROM domain_variations
                WHERE tld = 'com'
            ) WHERE rn = 1
            LIMIT ? OFFSET ?
        """, [batch_size, offset]).fetchall()
        return [row[0] for row in results]

    def get_total_unique_com(self) -> int:
        """Get total count of unique .com domains (one per LLC)."""
        conn = self._get_source_conn()
        result = conn.execute("""
            SELECT COUNT(DISTINCT llc_id)
            FROM domain_variations
            WHERE tld = 'com'
        """).fetchone()
        return result[0] if result else 0

    def save_taken_domains(self, domains: list[str]):
        """Save taken domains to output database."""
        if not domains:
            return
        conn = self._get_output_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO domain_variations (domain) VALUES (?)",
            [(d,) for d in domains]
        )

    def save_checkpoint(self):
        """Save progress checkpoint."""
        conn = self._get_output_conn()
        conn.execute("""
            INSERT OR REPLACE INTO harvest_checkpoint
            (id, source_offset, domains_checked, taken_count, updated_at)
            VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [self.offset, self.domains_checked, self.taken_count])

    def load_checkpoint(self) -> bool:
        """Load checkpoint if exists. Returns True if checkpoint found."""
        conn = self._get_output_conn()
        result = conn.execute("""
            SELECT source_offset, domains_checked, taken_count
            FROM harvest_checkpoint WHERE id = 1
        """).fetchone()

        if result:
            self.offset, self.domains_checked, self.taken_count = result
            return True
        return False

    def get_current_taken_count(self) -> int:
        """Get current count of taken domains in output DB."""
        conn = self._get_output_conn()
        result = conn.execute("SELECT COUNT(*) FROM domain_variations").fetchone()
        return result[0] if result else 0

    async def check_domain(self, domain: str, proxy) -> DomainResult:
        """Check single domain and return result."""
        proxy_dict = proxy.to_dict()
        result = await self.checker.check_single_domain(domain, proxy_dict)

        if result.status in ("taken", "available"):
            self.pool.report_success(proxy)
        else:
            self.pool.report_failure(proxy)

        return DomainResult(
            domain=result.domain,
            status=result.status,
            error=result.error
        )

    async def check_batch(self, domains: list[str], concurrency: int) -> list[DomainResult]:
        """Check batch of domains in parallel."""
        sem = asyncio.Semaphore(concurrency)
        proxies = self.pool.get_healthy_proxies()

        async def check_with_retry(domain: str, proxy_idx: int) -> DomainResult:
            async with sem:
                proxy = proxies[proxy_idx % len(proxies)]
                result = await self.check_domain(domain, proxy)

                for retry in range(MAX_RETRIES):
                    if result.status in ("taken", "available"):
                        break
                    proxy = proxies[(proxy_idx + retry + 1) % len(proxies)]
                    result = await self.check_domain(domain, proxy)

                return result

        tasks = [check_with_retry(domain, i) for i, domain in enumerate(domains)]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def harvest(
        self,
        target: int,
        batch_size: int = BATCH_SIZE,
        resume: bool = True
    ):
        """Harvest taken domains until target count reached."""
        self.start_time = time.perf_counter()

        # Resume from checkpoint if requested
        if resume and self.load_checkpoint():
            # Sync taken_count with actual DB count
            self.taken_count = self.get_current_taken_count()
            print(f"Resuming: offset={self.offset}, checked={self.domains_checked}, taken={self.taken_count}")
        else:
            self.offset = 0
            self.domains_checked = 0
            self.taken_count = self.get_current_taken_count()

        # Get source stats
        total_available = self.get_total_unique_com()
        concurrency = len(self.pool) * CONCURRENCY_PER_PROXY

        print(f"\nHarvest Taken Domains (Unique .com per LLC)")
        print("=" * 60)
        print(f"Source: {self.source_db}")
        print(f"  Unique .com domains: {total_available:,}")
        print(f"Output: {self.output_db}")
        print(f"Target: {target:,} taken domains")
        print(f"Current: {self.taken_count:,} taken domains")
        print(f"Proxies: {len(self.pool)}")
        print(f"uvloop: {'enabled' if UVLOOP else 'not available'}")
        print("-" * 60)

        batch_num = 0
        checkpoint_interval = 10000  # Save checkpoint every 10K checked

        while self.taken_count < target:
            # Get next batch
            domains = self.get_unique_com_domains(batch_size, self.offset)

            if not domains:
                print("\nExhausted all unique .com domains in source!")
                break

            batch_num += 1
            batch_start = time.perf_counter()

            # Check batch
            results = await self.check_batch(domains, concurrency)
            batch_time = time.perf_counter() - batch_start

            # Filter taken domains
            taken_domains = [r.domain for r in results if r.status == "taken"]

            # Save taken domains
            self.save_taken_domains(taken_domains)

            # Update stats
            self.domains_checked += len(results)
            self.taken_count += len(taken_domains)
            self.offset += len(domains)

            # Calculate rates
            elapsed = time.perf_counter() - self.start_time
            check_rate = self.domains_checked / elapsed if elapsed > 0 else 0
            batch_rate = len(results) / batch_time if batch_time > 0 else 0

            # Count results
            available = sum(1 for r in results if r.status == "available")
            errors = sum(1 for r in results if r.status in ("error", "unknown"))

            print(
                f"[Batch {batch_num}] "
                f"Checked: {self.domains_checked:,} | "
                f"T:{len(taken_domains)} A:{available} E:{errors} | "
                f"{batch_rate:.0f}/sec | "
                f"Taken: {self.taken_count:,}/{target:,} "
                f"({self.taken_count/target*100:.1f}%)"
            )

            # Checkpoint
            if self.domains_checked % checkpoint_interval < batch_size:
                self.save_checkpoint()

        # Final checkpoint
        self.save_checkpoint()

        # Summary
        self.print_summary(target)

    def print_summary(self, target: int):
        """Print harvest summary."""
        elapsed = time.perf_counter() - self.start_time
        checker_stats = self.checker.stats
        pool_stats = self.pool.summary()

        print("\n" + "=" * 60)
        print("HARVEST COMPLETE")
        print("=" * 60)
        print(f"Target:           {target:,}")
        print(f"Harvested:        {self.taken_count:,}")
        print(f"Domains checked:  {self.domains_checked:,}")
        print(f"Hit rate:         {self.taken_count/self.domains_checked*100:.1f}%" if self.domains_checked else "N/A")
        print()
        print(f"Time:             {elapsed:.1f}s ({elapsed/60:.1f} min)")
        print(f"Check rate:       {self.domains_checked/elapsed:.0f} domains/sec" if elapsed else "N/A")
        print()
        print(f"Proxies used:     {pool_stats['total']}")
        print(f"Proxies healthy:  {pool_stats['enabled']}")
        print(f"Success rate:     {pool_stats['overall_success_rate']*100:.1f}%")
        print()
        print(f"Bandwidth:        {(checker_stats.bytes_sent + checker_stats.bytes_received) / (1024*1024):.2f} MB")
        print()
        print(f"Output file:      {self.output_db}")

    def close(self):
        """Close database connections."""
        if self._source_conn:
            self._source_conn.close()
        if self._output_conn:
            self._output_conn.close()


async def main():
    parser = argparse.ArgumentParser(description="Harvest Taken Domains")
    parser.add_argument("--target", type=int, default=100000, help="Number of taken domains to collect (default: 100000)")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT), help="Output DuckDB file path")
    parser.add_argument("--source", type=str, default=str(VARIATIONS_DUCKDB), help="Source domain_variations.duckdb path")
    parser.add_argument("--max-proxies", type=int, default=None, help="Limit number of proxies")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Domains per batch")
    parser.add_argument("--no-resume", action="store_true", help="Start fresh, don't resume from checkpoint")

    args = parser.parse_args()

    # Get proxy file from environment or default
    from whois_checker import PROXY_FILE

    output_path = Path(args.output)
    source_path = Path(args.source)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    harvester = TakenHarvester(
        source_db=source_path,
        output_db=output_path,
        proxy_file=PROXY_FILE,
        max_proxies=args.max_proxies
    )

    try:
        await harvester.harvest(
            target=args.target,
            batch_size=args.batch_size,
            resume=not args.no_resume
        )
    finally:
        harvester.close()


if __name__ == "__main__":
    asyncio.run(main())
