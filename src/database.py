#!/usr/bin/env python3
"""
Database Integration (DuckDB)

Reads domains from domain_variations.duckdb, writes results to domain_checks.duckdb.
Tracks which domains have been checked for resume capability.

Environment Variables:
    VARIATIONS_DB: Path to domain variations DuckDB file
    CHECKS_DB: Path to results DuckDB file (will be created if not exists)
"""

import os
import duckdb
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from datetime import datetime


# Default paths (can be overridden by environment variables)
VARIATIONS_DUCKDB = Path(os.environ.get(
    "VARIATIONS_DB",
    "/Users/collinsoik/Desktop/Code_Space/rdap_test/domain_variations.duckdb"
))
CHECKS_DUCKDB = Path(os.environ.get(
    "CHECKS_DB",
    "/Users/collinsoik/Desktop/Code_Space/rdap_test/domain_checks.duckdb"
))


@dataclass
class DomainResult:
    """Result of a domain check."""
    domain: str
    status: str  # 'taken', 'available', 'error', 'unknown'
    error: Optional[str] = None
    checked_at: Optional[datetime] = None


class DomainDatabase:
    """Manages reading domain variations and writing check results using DuckDB."""

    def __init__(
        self,
        variations_db: Path = VARIATIONS_DUCKDB,
        checks_db: Path = CHECKS_DUCKDB
    ):
        self.variations_db = variations_db
        self.checks_db = checks_db
        self._variations_conn = None  # Lazy, read-only
        self._checks_conn = None      # Lazy, read-write
        self._init_checks_db()

    def _get_variations_conn(self):
        """Get or create DuckDB connection for variations (read-only)."""
        if self._variations_conn is None:
            self._variations_conn = duckdb.connect(str(self.variations_db), read_only=True)
        return self._variations_conn

    def _get_checks_conn(self):
        """Get or create DuckDB connection for checks (read-write)."""
        if self._checks_conn is None:
            self._checks_conn = duckdb.connect(str(self.checks_db))
        return self._checks_conn

    def _init_checks_db(self):
        """Initialize the checks database with results table."""
        conn = self._get_checks_conn()

        # Create domain_checks table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS domain_checks (
                domain VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                error VARCHAR,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create checkpoints table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY,
                last_offset BIGINT,
                total_checked BIGINT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create index for status queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON domain_checks(status)
        """)

    def get_total_domains(self) -> int:
        """Get total number of domains to check."""
        conn = self._get_variations_conn()
        result = conn.execute("SELECT COUNT(*) FROM domain_variations").fetchone()
        return result[0] if result else 0

    def get_checked_count(self) -> int:
        """Get number of domains already checked."""
        conn = self._get_checks_conn()
        result = conn.execute("SELECT COUNT(*) FROM domain_checks").fetchone()
        return result[0] if result else 0

    def get_unchecked_domains(
        self,
        batch_size: int = 10000,
        offset: int = 0
    ) -> list[str]:
        """
        Get batch of unchecked domains.
        Uses two-phase approach: get from variations, filter against checks.
        """
        # Get candidates from variations
        candidates = self.get_domains_batch(batch_size * 2, offset)
        if not candidates:
            return []

        # Filter against checks
        conn = self._get_checks_conn()
        placeholders = ','.join(['?' for _ in candidates])
        query = f"SELECT domain FROM domain_checks WHERE domain IN ({placeholders})"
        checked = set(row[0] for row in conn.execute(query, candidates).fetchall())

        return [d for d in candidates if d not in checked][:batch_size]

    def get_domains_batch(
        self,
        batch_size: int = 10000,
        offset: int = 0
    ) -> list[str]:
        """Get batch of domains using simple offset (faster for sequential reads)."""
        conn = self._get_variations_conn()
        query = "SELECT domain FROM domain_variations LIMIT ? OFFSET ?"
        results = conn.execute(query, [batch_size, offset]).fetchall()
        return [row[0] for row in results]

    def save_results(self, results: list[DomainResult]):
        """Save batch of results to database."""
        if not results:
            return

        conn = self._get_checks_conn()
        conn.executemany(
            """
            INSERT OR REPLACE INTO domain_checks (domain, status, error, checked_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [(r.domain, r.status, r.error) for r in results]
        )

    def save_checkpoint(self, offset: int, total_checked: int):
        """Save checkpoint for resume."""
        conn = self._get_checks_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO checkpoints (id, last_offset, total_checked, updated_at)
            VALUES (1, ?, ?, CURRENT_TIMESTAMP)
            """,
            [offset, total_checked]
        )

    def get_checkpoint(self) -> tuple[int, int]:
        """Get last checkpoint (offset, total_checked)."""
        conn = self._get_checks_conn()
        result = conn.execute(
            "SELECT last_offset, total_checked FROM checkpoints WHERE id = 1"
        ).fetchone()
        return (result[0], result[1]) if result else (0, 0)

    def get_stats(self) -> dict:
        """Get check statistics."""
        conn = self._get_checks_conn()
        stats = {}

        # Count by status
        for row in conn.execute(
            "SELECT status, COUNT(*) FROM domain_checks GROUP BY status"
        ).fetchall():
            stats[row[0]] = row[1]

        # Total
        stats['total'] = sum(stats.values())

        return stats

    def close(self):
        """Close database connections."""
        if self._variations_conn is not None:
            self._variations_conn.close()
            self._variations_conn = None
        if self._checks_conn is not None:
            self._checks_conn.close()
            self._checks_conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def create_test_database(num_domains: int = 1000) -> Path:
    """Create a test variations database for testing (DuckDB format)."""
    test_db = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_variations.duckdb")

    # Remove if exists
    if test_db.exists():
        test_db.unlink()

    conn = duckdb.connect(str(test_db))
    conn.execute("""
        CREATE TABLE domain_variations (
            domain VARCHAR PRIMARY KEY
        )
    """)

    # Generate mix of real and fake domains
    real = ["google.com", "amazon.com", "microsoft.com", "github.com", "apple.com",
            "facebook.com", "twitter.com", "netflix.com", "linkedin.com", "youtube.com"]
    domains = []

    for i in range(num_domains):
        if i < len(real):  # First 10 are real domains
            domains.append((real[i],))
        else:
            domains.append((f"testllc{i:08d}.com",))

    conn.executemany(
        "INSERT INTO domain_variations (domain) VALUES (?)",
        domains
    )
    conn.close()

    print(f"Created test database with {num_domains} domains: {test_db}")
    return test_db


# Test
if __name__ == "__main__":
    print("=" * 60)
    print("DATABASE INTEGRATION TEST (DuckDB)")
    print("=" * 60)

    # Create test database
    test_variations = create_test_database(1000)
    test_checks = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_checks.duckdb")

    # Remove old checks if exists
    if test_checks.exists():
        test_checks.unlink()

    # Initialize
    db = DomainDatabase(
        variations_db=test_variations,
        checks_db=test_checks
    )

    print(f"\nTotal domains: {db.get_total_domains()}")
    print(f"Already checked: {db.get_checked_count()}")

    # Get batch
    batch = db.get_domains_batch(batch_size=100)
    print(f"\nFirst batch: {len(batch)} domains")
    print(f"  First 5: {batch[:5]}")

    # Simulate some results
    results = [
        DomainResult(batch[0], "taken"),
        DomainResult(batch[1], "available"),
        DomainResult(batch[2], "available"),
        DomainResult(batch[3], "error", "timeout"),
        DomainResult(batch[4], "taken"),
    ]

    db.save_results(results)
    print(f"\nSaved {len(results)} results")

    # Check stats
    stats = db.get_stats()
    print(f"\nStats: {stats}")

    # Test checkpoint
    db.save_checkpoint(offset=100, total_checked=100)
    checkpoint = db.get_checkpoint()
    print(f"\nCheckpoint: offset={checkpoint[0]}, checked={checkpoint[1]}")

    # Get unchecked domains
    unchecked = db.get_unchecked_domains(batch_size=10)
    print(f"\nUnchecked domains (first 10): {len(unchecked)}")
    print(f"  {unchecked[:5]}")

    # Cleanup
    db.close()

    print("\n" + "=" * 60)
    print("DATABASE INTEGRATION TEST PASSED")
    print("=" * 60)
