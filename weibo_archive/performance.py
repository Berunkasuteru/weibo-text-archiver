from __future__ import annotations

import math
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable


REQUEST_CATEGORIES = (
    "preheat",
    "profile_basic",
    "profile_detail",
    "timeline",
    "longtext_extend",
    "longtext_detail",
)
WAIT_CATEGORIES = (
    "profile_pacing",
    "page_pacing",
    "longtext_pacing",
    "batch_rest",
    "session_rest",
)
RETRY_WAIT_CATEGORIES = (
    "ordinary_backoff",
    "http_429_cooldown",
    "http_432_cooldown",
)
LONGTEXT_COUNTERS = (
    "unique_attempted",
    "extend_success",
    "detail_attempted",
    "detail_success",
    "content_unavailable",
)
ERROR_COUNTERS = (
    "ordinary_retry",
    "http_403",
    "http_414",
    "http_429",
    "http_432",
    "auth_expired",
    "challenge",
)


def _latency_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": len(ordered),
        "mean": sum(ordered) / len(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[p95_index],
    }


@dataclass
class PerformanceMetrics:
    clock: Callable[[], float] = time.monotonic
    request_latencies: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    planned_waits: Counter = field(default_factory=Counter)
    retry_waits: Counter = field(default_factory=Counter)
    longtext: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    _started_at: float | None = None
    _last_request_started_at: float | None = None
    total_wall_time: float | None = None
    successful: bool | None = None

    def begin(self) -> None:
        self._started_at = self.clock()
        self.total_wall_time = None
        self.successful = None

    def finish(self, *, successful: bool) -> None:
        if self._started_at is None:
            return
        self.total_wall_time = max(0.0, self.clock() - self._started_at)
        self.successful = successful

    def record_request(self, category: str, elapsed: float) -> None:
        key = category if category in REQUEST_CATEGORIES else "other"
        self.request_latencies[key].append(max(0.0, elapsed))

    def note_request_start(self) -> None:
        self._last_request_started_at = self.clock()

    def remaining_start_spacing(self, interval: float) -> float:
        interval = max(0.0, interval)
        if self._last_request_started_at is None:
            return interval
        elapsed = max(0.0, self.clock() - self._last_request_started_at)
        return max(0.0, interval - elapsed)

    def record_planned_wait(self, category: str, seconds: float) -> None:
        key = category if category in WAIT_CATEGORIES else "other"
        self.planned_waits[key] += max(0.0, seconds)

    def record_retry_wait(self, category: str, seconds: float) -> None:
        key = category if category in RETRY_WAIT_CATEGORIES else "other"
        self.retry_waits[key] += max(0.0, seconds)

    def increment_longtext(self, category: str) -> None:
        key = category if category in LONGTEXT_COUNTERS else "other"
        self.longtext[key] += 1

    def increment_error(self, category: str) -> None:
        key = category if category in ERROR_COUNTERS else "other"
        self.errors[key] += 1

    def snapshot(self) -> dict:
        requests = {
            category: _latency_summary(list(self.request_latencies.get(category, ())))
            for category in (*REQUEST_CATEGORIES, "other")
        }
        request_time = sum(sum(values) for values in self.request_latencies.values())
        planned_wait_time = sum(self.planned_waits.values())
        retry_wait_time = sum(self.retry_waits.values())
        total = self.total_wall_time or 0.0
        other = max(0.0, total - request_time - planned_wait_time - retry_wait_time)
        return {
            "status": (
                "fetch_success" if self.successful is True else
                "fetch_failed" if self.successful is False else
                "in_progress"
            ),
            "total_wall_time": total,
            "request_time": request_time,
            "planned_wait_time": planned_wait_time,
            "retry_wait_time": retry_wait_time,
            "other_time": other,
            "request_count": sum(item["count"] for item in requests.values()),
            "requests": requests,
            "planned_waits": {
                key: float(self.planned_waits.get(key, 0.0))
                for key in (*WAIT_CATEGORIES, "other")
            },
            "retry_waits": {
                key: float(self.retry_waits.get(key, 0.0))
                for key in (*RETRY_WAIT_CATEGORIES, "other")
            },
            "longtext": {
                key: int(self.longtext.get(key, 0)) for key in LONGTEXT_COUNTERS
            },
            "errors": {
                key: int(self.errors.get(key, 0)) for key in ERROR_COUNTERS
            },
        }

    def render(self) -> str:
        data = self.snapshot()
        lines = [
            "Performance",
            f"  status: {data['status']}",
            f"  total: {data['total_wall_time']:.3f}s",
            f"  HTTP: {data['request_time']:.3f}s / {data['request_count']} requests",
            f"  planned waits: {data['planned_wait_time']:.3f}s",
            f"  retry waits: {data['retry_wait_time']:.3f}s",
            f"  other: {data['other_time']:.3f}s",
        ]
        for category in REQUEST_CATEGORIES:
            item = data["requests"][category]
            if item["count"]:
                lines.append(
                    f"  {category}: n={item['count']} mean={item['mean']:.3f}s "
                    f"p50={item['p50']:.3f}s p95={item['p95']:.3f}s"
                )
        waits = [
            f"{key}={value:.3f}s"
            for key, value in data["planned_waits"].items()
            if value
        ]
        if waits:
            lines.append("  planned: " + ", ".join(waits))
        retry_waits = [
            f"{key}={value:.3f}s"
            for key, value in data["retry_waits"].items()
            if value
        ]
        if retry_waits:
            lines.append("  retry: " + ", ".join(retry_waits))
        longtext = data["longtext"]
        lines.append(
            "  longtext: "
            + ", ".join(f"{key}={longtext[key]}" for key in LONGTEXT_COUNTERS)
        )
        restrictions = [
            f"{key}={value}" for key, value in data["errors"].items() if value
        ]
        if restrictions:
            lines.append("  restrictions/errors: " + ", ".join(restrictions))
        return "\n".join(lines)
