#!/usr/bin/env python3
"""
Adaptive rate controller for domain checking.

Dynamically adjusts concurrency based on latency and timeout metrics
to maximize throughput while avoiding rate limit penalties.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from metrics import MetricsSnapshot, RollingMetrics


class ControllerState(Enum):
    """Controller operating state."""
    RAMPING_UP = "ramping_up"      # Increasing concurrency
    STABLE = "stable"              # Maintaining current rate
    BACKING_OFF = "backing_off"    # Decreasing concurrency
    PAUSED = "paused"              # Temporarily paused due to severe throttling


@dataclass
class ControllerConfig:
    """Configuration for the adaptive rate controller."""
    # Concurrency limits
    min_concurrency: int = 50
    max_concurrency: int = 500
    initial_concurrency: int = 300

    # Adjustment rates
    increase_factor: float = 1.10  # 10% increase
    decrease_factor: float = 0.80  # 20% decrease
    severe_decrease_factor: float = 0.50  # 50% decrease

    # Thresholds for action
    latency_low_ms: float = 120.0      # Below this: can increase
    latency_high_ms: float = 200.0     # Above this: should decrease
    latency_critical_ms: float = 500.0 # Above this: severe backoff

    timeout_warning: float = 0.01      # 1% timeout rate: warning
    timeout_high: float = 0.02         # 2% timeout rate: decrease
    timeout_critical: float = 0.05     # 5% timeout rate: pause

    # Timing
    check_interval: int = 500          # Check every N queries
    pause_duration: float = 30.0       # Seconds to pause when critical
    min_stable_duration: float = 5.0   # Seconds before allowing increase

    # Stability requirements for increasing
    stable_queries_required: int = 1000  # Need this many good queries to increase


class AdaptiveRateController:
    """
    Adaptive rate controller that adjusts concurrency based on metrics.

    The controller monitors latency and timeout rates and adjusts
    concurrency to maximize throughput while avoiding rate limiting.
    """

    def __init__(self, config: Optional[ControllerConfig] = None):
        self.config = config or ControllerConfig()

        # Current state
        self.concurrency = self.config.initial_concurrency
        self.state = ControllerState.STABLE
        self.queries_since_check = 0
        self.queries_since_adjustment = 0

        # Timing
        self.last_adjustment_time = time.time()
        self.last_decrease_time = 0.0
        self.pause_until = 0.0

        # History for logging
        self.adjustment_history: list[tuple[float, int, str]] = []

    def update(self, metrics: MetricsSnapshot) -> int:
        """
        Update controller with current metrics and return new concurrency.

        Args:
            metrics: Current metrics snapshot

        Returns:
            New concurrency value to use
        """
        self.queries_since_check += 1

        # Check if we're paused
        if self.state == ControllerState.PAUSED:
            if time.time() >= self.pause_until:
                self._resume_from_pause()
            return self.concurrency

        # Only check periodically
        if self.queries_since_check < self.config.check_interval:
            return self.concurrency

        self.queries_since_check = 0
        return self._evaluate_and_adjust(metrics)

    def _evaluate_and_adjust(self, metrics: MetricsSnapshot) -> int:
        """Evaluate metrics and adjust concurrency if needed."""
        now = time.time()
        old_concurrency = self.concurrency
        action = "none"

        # Check for critical conditions first
        if metrics.timeout_rate >= self.config.timeout_critical:
            # Critical: pause and reset
            self._pause()
            action = f"PAUSE (timeout={metrics.timeout_rate*100:.1f}%)"

        elif metrics.timeout_rate >= self.config.timeout_high:
            # High timeout: aggressive decrease
            self._decrease(severe=True)
            action = f"SEVERE_DECREASE (timeout={metrics.timeout_rate*100:.1f}%)"

        elif metrics.p95_latency_ms >= self.config.latency_critical_ms:
            # Critical latency: aggressive decrease
            self._decrease(severe=True)
            action = f"SEVERE_DECREASE (p95={metrics.p95_latency_ms:.0f}ms)"

        elif metrics.avg_latency_ms >= self.config.latency_high_ms:
            # High latency: normal decrease
            self._decrease(severe=False)
            action = f"DECREASE (avg_lat={metrics.avg_latency_ms:.0f}ms)"

        elif metrics.timeout_rate >= self.config.timeout_warning:
            # Warning level timeout: decrease slightly
            self._decrease(severe=False)
            action = f"DECREASE (timeout={metrics.timeout_rate*100:.1f}%)"

        elif self._can_increase(metrics, now):
            # Good conditions: try to increase
            self._increase()
            action = f"INCREASE (avg_lat={metrics.avg_latency_ms:.0f}ms)"

        # Log adjustment if changed
        if self.concurrency != old_concurrency:
            self.adjustment_history.append((now, self.concurrency, action))
            self.last_adjustment_time = now
            self.queries_since_adjustment = 0

        return self.concurrency

    def _can_increase(self, metrics: MetricsSnapshot, now: float) -> bool:
        """Check if conditions are good for increasing concurrency."""
        # Already at max
        if self.concurrency >= self.config.max_concurrency:
            return False

        # Need stable period after last decrease
        if now - self.last_decrease_time < self.config.min_stable_duration:
            return False

        # Need enough queries since last adjustment
        if self.queries_since_adjustment < self.config.stable_queries_required:
            return False

        # Check latency is low enough
        if metrics.avg_latency_ms > self.config.latency_low_ms:
            return False

        # Check no significant timeouts
        if metrics.timeout_rate > self.config.timeout_warning / 2:
            return False

        return True

    def _increase(self):
        """Increase concurrency."""
        new_concurrency = int(self.concurrency * self.config.increase_factor)
        self.concurrency = min(new_concurrency, self.config.max_concurrency)
        self.state = ControllerState.RAMPING_UP

    def _decrease(self, severe: bool = False):
        """Decrease concurrency."""
        factor = self.config.severe_decrease_factor if severe else self.config.decrease_factor
        new_concurrency = int(self.concurrency * factor)
        self.concurrency = max(new_concurrency, self.config.min_concurrency)
        self.state = ControllerState.BACKING_OFF
        self.last_decrease_time = time.time()

    def _pause(self):
        """Pause due to critical rate limiting."""
        self.state = ControllerState.PAUSED
        self.pause_until = time.time() + self.config.pause_duration
        # Reset to half of current concurrency after pause
        self.concurrency = max(
            self.concurrency // 2,
            self.config.min_concurrency
        )

    def _resume_from_pause(self):
        """Resume after pause period."""
        self.state = ControllerState.STABLE
        self.queries_since_adjustment = 0

    def should_pause(self) -> bool:
        """Check if currently paused."""
        return self.state == ControllerState.PAUSED and time.time() < self.pause_until

    def get_pause_remaining(self) -> float:
        """Get seconds remaining in pause."""
        if not self.should_pause():
            return 0.0
        return max(0.0, self.pause_until - time.time())

    def get_concurrency(self) -> int:
        """Get current concurrency value."""
        return self.concurrency

    def get_state(self) -> ControllerState:
        """Get current controller state."""
        return self.state

    def get_status_str(self) -> str:
        """Get human-readable status string."""
        if self.state == ControllerState.PAUSED:
            remaining = self.get_pause_remaining()
            return f"PAUSED ({remaining:.0f}s remaining) concurrency={self.concurrency}"
        return f"{self.state.value} concurrency={self.concurrency}"

    def record_queries(self, count: int):
        """Record that queries were processed (for stability tracking)."""
        self.queries_since_adjustment += count


# Convenience function for creating controller with env var overrides
def create_controller_from_env() -> AdaptiveRateController:
    """Create controller with configuration from environment variables."""
    import os

    config = ControllerConfig(
        min_concurrency=int(os.environ.get("RATE_MIN_CONCURRENCY", 50)),
        max_concurrency=int(os.environ.get("RATE_MAX_CONCURRENCY", 500)),
        initial_concurrency=int(os.environ.get("RATE_INITIAL_CONCURRENCY", 300)),
        latency_low_ms=float(os.environ.get("RATE_LATENCY_LOW_MS", 120)),
        latency_high_ms=float(os.environ.get("RATE_LATENCY_HIGH_MS", 200)),
        timeout_high=float(os.environ.get("RATE_TIMEOUT_HIGH", 0.02)),
        pause_duration=float(os.environ.get("RATE_PAUSE_DURATION", 30)),
    )

    return AdaptiveRateController(config)
