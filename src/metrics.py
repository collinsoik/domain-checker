#!/usr/bin/env python3
"""
Rolling metrics tracker for adaptive rate control.

Tracks latency, timeouts, and throughput in rolling windows
to detect rate limiting and adjust concurrency dynamically.
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class MetricsSnapshot:
    """Point-in-time metrics snapshot."""
    avg_latency_ms: float
    p95_latency_ms: float
    timeout_rate: float  # 0.0 to 1.0
    throughput: float  # queries per second
    total_queries: int
    total_timeouts: int


class RollingMetrics:
    """
    Rolling window metrics tracker.

    Tracks:
    - Latencies (last N queries) for avg/p95 calculation
    - Timeouts (last M queries) for timeout rate
    - Timestamps for throughput calculation
    """

    def __init__(
        self,
        latency_window: int = 100,
        timeout_window: int = 1000,
        throughput_window: float = 10.0  # seconds
    ):
        self.latency_window = latency_window
        self.timeout_window = timeout_window
        self.throughput_window = throughput_window

        # Rolling windows
        self.latencies: deque[float] = deque(maxlen=latency_window)
        self.timeouts: deque[bool] = deque(maxlen=timeout_window)
        self.timestamps: deque[float] = deque()

        # Totals
        self.total_queries = 0
        self.total_timeouts = 0

        # Last update time
        self.last_update = time.time()

    def record(self, latency_ms: float, is_timeout: bool = False):
        """Record a single query result."""
        now = time.time()

        # Record latency (only for successful queries)
        if not is_timeout and latency_ms > 0:
            self.latencies.append(latency_ms)

        # Record timeout status
        self.timeouts.append(is_timeout)

        # Record timestamp for throughput
        self.timestamps.append(now)

        # Update totals
        self.total_queries += 1
        if is_timeout:
            self.total_timeouts += 1

        # Clean old timestamps outside throughput window
        cutoff = now - self.throughput_window
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

        self.last_update = now

    def record_batch(self, latencies_ms: list[float], timeouts: list[bool]):
        """Record a batch of query results."""
        for latency, is_timeout in zip(latencies_ms, timeouts):
            self.record(latency, is_timeout)

    def get_avg_latency(self) -> float:
        """Get average latency in ms (last N queries)."""
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)

    def get_p95_latency(self) -> float:
        """Get 95th percentile latency in ms."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    def get_p99_latency(self) -> float:
        """Get 99th percentile latency in ms."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    def get_timeout_rate(self) -> float:
        """Get timeout rate (0.0 to 1.0) over last M queries."""
        if not self.timeouts:
            return 0.0
        return sum(1 for t in self.timeouts if t) / len(self.timeouts)

    def get_throughput(self) -> float:
        """Get current throughput (queries per second)."""
        if len(self.timestamps) < 2:
            return 0.0

        now = time.time()
        cutoff = now - self.throughput_window

        # Count queries in window
        queries_in_window = sum(1 for t in self.timestamps if t >= cutoff)

        if queries_in_window == 0:
            return 0.0

        # Calculate actual time span
        recent_timestamps = [t for t in self.timestamps if t >= cutoff]
        if len(recent_timestamps) < 2:
            return 0.0

        time_span = recent_timestamps[-1] - recent_timestamps[0]
        if time_span <= 0:
            return 0.0

        return queries_in_window / time_span

    def get_snapshot(self) -> MetricsSnapshot:
        """Get current metrics snapshot."""
        return MetricsSnapshot(
            avg_latency_ms=self.get_avg_latency(),
            p95_latency_ms=self.get_p95_latency(),
            timeout_rate=self.get_timeout_rate(),
            throughput=self.get_throughput(),
            total_queries=self.total_queries,
            total_timeouts=self.total_timeouts
        )

    def reset(self):
        """Reset all metrics."""
        self.latencies.clear()
        self.timeouts.clear()
        self.timestamps.clear()
        self.total_queries = 0
        self.total_timeouts = 0
        self.last_update = time.time()

    def __str__(self) -> str:
        snapshot = self.get_snapshot()
        return (
            f"Metrics: avg={snapshot.avg_latency_ms:.0f}ms "
            f"p95={snapshot.p95_latency_ms:.0f}ms "
            f"timeout={snapshot.timeout_rate*100:.1f}% "
            f"throughput={snapshot.throughput:.0f}/sec"
        )
