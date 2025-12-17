#!/usr/bin/env python3
"""
Iteration 2: Proxy Pool Management

Manages multiple proxies with:
- Round-robin distribution
- Health tracking (success/failure rates)
- Automatic proxy rotation
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import random


# Default proxy file path (cross-platform)
_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
PROXY_FILE = _DEFAULT_DATA_DIR / "proxies.txt"


@dataclass
class ProxyStats:
    """Track proxy performance."""
    success: int = 0
    failures: int = 0

    @property
    def total(self) -> int:
        return self.success + self.failures

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.success / self.total


@dataclass
class Proxy:
    """Proxy configuration."""
    host: str
    port: int
    user: str
    password: str
    stats: ProxyStats = field(default_factory=ProxyStats)
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "pass": self.password
        }

    def __hash__(self):
        return hash((self.host, self.port))


class ProxyPool:
    """Manages a pool of proxies with health tracking."""

    def __init__(self, proxy_file: Path = PROXY_FILE, max_proxies: Optional[int] = None):
        self.proxies = self._load_proxies(proxy_file, max_proxies)
        self._index = 0

    def _load_proxies(self, proxy_file: Path, max_proxies: Optional[int] = None) -> list[Proxy]:
        """Load proxies from file."""
        proxies = []
        with open(proxy_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    auth, hostport = line.split("@")
                    user, passwd = auth.split(":")
                    host, port = hostport.split(":")
                    proxies.append(Proxy(
                        host=host,
                        port=int(port),
                        user=user,
                        password=passwd
                    ))
                except ValueError:
                    continue

                if max_proxies and len(proxies) >= max_proxies:
                    break

        return proxies

    def get_proxy(self) -> Proxy:
        """Get next healthy proxy (round-robin)."""
        start_idx = self._index
        while True:
            proxy = self.proxies[self._index]
            self._index = (self._index + 1) % len(self.proxies)

            if proxy.enabled:
                return proxy

            # Wrapped around without finding enabled proxy
            if self._index == start_idx:
                # Re-enable all proxies if all disabled
                for p in self.proxies:
                    p.enabled = True
                return self.proxies[0]

    def get_proxies(self, count: int) -> list[Proxy]:
        """Get N proxies for parallel work."""
        return [self.get_proxy() for _ in range(count)]

    def report_success(self, proxy: Proxy):
        """Report successful query."""
        proxy.stats.success += 1

    def report_failure(self, proxy: Proxy):
        """Report failed query and potentially disable proxy."""
        proxy.stats.failures += 1

        # Disable proxy if success rate drops below 50% after 10+ attempts
        if proxy.stats.total >= 10 and proxy.stats.success_rate < 0.5:
            proxy.enabled = False

    def get_healthy_proxies(self) -> list[Proxy]:
        """Get all enabled proxies."""
        return [p for p in self.proxies if p.enabled]

    def distribute_domains(self, domains: list[str], domains_per_proxy: int = 10) -> list[tuple[list[str], Proxy]]:
        """
        Distribute domains across proxies.
        Returns list of (domain_batch, proxy) tuples.
        """
        batches = []
        healthy = self.get_healthy_proxies()

        for i in range(0, len(domains), domains_per_proxy):
            batch = domains[i:i + domains_per_proxy]
            proxy = healthy[i // domains_per_proxy % len(healthy)]
            batches.append((batch, proxy))

        return batches

    def __len__(self) -> int:
        return len(self.proxies)

    def summary(self) -> dict:
        """Get pool summary stats."""
        enabled = [p for p in self.proxies if p.enabled]
        total_success = sum(p.stats.success for p in self.proxies)
        total_failures = sum(p.stats.failures for p in self.proxies)

        return {
            "total": len(self.proxies),
            "enabled": len(enabled),
            "disabled": len(self.proxies) - len(enabled),
            "total_success": total_success,
            "total_failures": total_failures,
            "overall_success_rate": total_success / (total_success + total_failures) if (total_success + total_failures) > 0 else 1.0
        }


# Test
if __name__ == "__main__":
    pool = ProxyPool(max_proxies=50)
    print(f"Loaded {len(pool)} proxies")

    # Show first 3
    for i, p in enumerate(pool.proxies[:3]):
        print(f"  {i+1}. {p.host}:{p.port}")

    # Test distribution
    domains = [f"test{i}.com" for i in range(100)]
    batches = pool.distribute_domains(domains, domains_per_proxy=10)
    print(f"\nDistributed {len(domains)} domains into {len(batches)} batches")

    # Show batch distribution
    proxy_usage = {}
    for batch, proxy in batches:
        key = f"{proxy.host}:{proxy.port}"
        proxy_usage[key] = proxy_usage.get(key, 0) + len(batch)

    print(f"Proxies used: {len(proxy_usage)}")
    for proxy, count in list(proxy_usage.items())[:5]:
        print(f"  {proxy}: {count} domains")
