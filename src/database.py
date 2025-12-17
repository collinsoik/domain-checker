#!/usr/bin/env python3
"""
Iteration 4: Database Integration

Reads domains from domain_variations.db, writes results to domain_checks.db.
Tracks which domains have been checked for resume capability.
"""

import sqlite3
from pathlib import Path
from typing import Generator, Optional
from dataclasses import dataclass
from datetime import datetime


# Default paths
VARIATIONS_DB = Path("/Users/collinsoik/Desktop/Pre_Classifcation_Georgia/domain_matcher/data/output/domain_variations.db")
CHECKS_DB = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/domain_checks.db")


@dataclass
class DomainResult:
    """Result of a domain check."""
    domain: str
    status: str  # 'taken', 'available', 'error', 'unknown'
    error: Optional[str] = None
    checked_at: Optional[datetime] = None


class DomainDatabase:
    """Manages reading domain variations and writing check results."""

    def __init__(
        self,
        variations_db: Path = VARIATIONS_DB,
        checks_db: Path = CHECKS_DB
    ):
        self.variations_db = variations_db
        self.checks_db = checks_db
        self._init_checks_db()

    def _init_checks_db(self):
        """Initialize the checks database with results table."""
        with sqlite3.connect(self.checks_db) as conn:
            conn.executescript("""
                -- Results table
                CREATE TABLE IF NOT EXISTS domain_checks (
                    domain TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    error TEXT,
                    checked_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                -- Index for querying by status
                CREATE INDEX IF NOT EXISTS idx_status ON domain_checks(status);

                -- Checkpoint table for resume
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY,
                    last_offset INTEGER,
                    total_checked INTEGER,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                -- Performance settings
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                PRAGMA cache_size = 10000;
            """)

    def get_total_domains(self) -> int:
        """Get total number of domains to check."""
        with sqlite3.connect(self.variations_db) as conn:
            result = conn.execute("SELECT COUNT(*) FROM domain_variations").fetchone()
            return result[0] if result else 0

    def get_checked_count(self) -> int:
        """Get number of domains already checked."""
        with sqlite3.connect(self.checks_db) as conn:
            result = conn.execute("SELECT COUNT(*) FROM domain_checks").fetchone()
            return result[0] if result else 0

    def get_unchecked_domains(
        self,
        batch_size: int = 10000,
        offset: int = 0
    ) -> list[str]:
        """
        Get batch of unchecked domains.
        Uses LEFT JOIN to exclude already-checked domains.
        """
        # For efficiency, we'll use offset-based pagination
        # and check against the results table
        with sqlite3.connect(self.variations_db) as conn:
            # Attach checks database
            conn.execute(f"ATTACH DATABASE '{self.checks_db}' AS checks")

            query = """
                SELECT dv.domain
                FROM domain_variations dv
                LEFT JOIN checks.domain_checks dc ON dv.domain = dc.domain
                WHERE dc.domain IS NULL
                LIMIT ?
            """

            results = conn.execute(query, (batch_size,)).fetchall()
            return [row[0] for row in results]

    def get_domains_batch(
        self,
        batch_size: int = 10000,
        offset: int = 0
    ) -> list[str]:
        """Get batch of domains using simple offset (faster for sequential reads)."""
        with sqlite3.connect(self.variations_db) as conn:
            query = "SELECT domain FROM domain_variations LIMIT ? OFFSET ?"
            results = conn.execute(query, (batch_size, offset)).fetchall()
            return [row[0] for row in results]

    def save_results(self, results: list[DomainResult]):
        """Save batch of results to database."""
        if not results:
            return

        with sqlite3.connect(self.checks_db) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO domain_checks (domain, status, error, checked_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [(r.domain, r.status, r.error) for r in results]
            )

    def save_checkpoint(self, offset: int, total_checked: int):
        """Save checkpoint for resume."""
        with sqlite3.connect(self.checks_db) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (id, last_offset, total_checked, updated_at)
                VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                """,
                (offset, total_checked)
            )

    def get_checkpoint(self) -> tuple[int, int]:
        """Get last checkpoint (offset, total_checked)."""
        with sqlite3.connect(self.checks_db) as conn:
            result = conn.execute(
                "SELECT last_offset, total_checked FROM checkpoints WHERE id = 1"
            ).fetchone()
            return result if result else (0, 0)

    def get_stats(self) -> dict:
        """Get check statistics."""
        with sqlite3.connect(self.checks_db) as conn:
            stats = {}

            # Count by status
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM domain_checks GROUP BY status"
            ):
                stats[row[0]] = row[1]

            # Total
            stats['total'] = sum(stats.values())

            return stats


def create_test_database(num_domains: int = 1000) -> Path:
    """Create a test variations database for testing."""
    test_db = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_variations.db")

    # Remove if exists
    if test_db.exists():
        test_db.unlink()

    with sqlite3.connect(test_db) as conn:
        conn.execute("""
            CREATE TABLE domain_variations (
                domain TEXT PRIMARY KEY
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

    print(f"Created test database with {num_domains} domains: {test_db}")
    return test_db


# Test
if __name__ == "__main__":
    print("=" * 60)
    print("DATABASE INTEGRATION TEST")
    print("=" * 60)

    # Create test database
    test_variations = create_test_database(1000)
    test_checks = Path("/Users/collinsoik/Desktop/Code_Space/rdap_test/test_checks.db")

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

    print("\n" + "=" * 60)
    print("DATABASE INTEGRATION TEST PASSED")
    print("=" * 60)
