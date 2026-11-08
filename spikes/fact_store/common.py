"""Shared helpers for the fact store spikes (S-01, S-02)."""

import os
import statistics
import time
from collections.abc import Callable
from typing import Any

import psycopg

URL = os.environ.get("SPIKE_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dwh")

# Dimension ids and hierarchy ids used throughout the spike.
ENTITY, COST_CENTER, ACCOUNT, COUNTRY, PRODUCT = 1, 2, 3, 4, 5
H_CC, H_CC_FUNCTION, H_ACCOUNT, H_COUNTRY, H_PRODUCT = 1, 2, 3, 4, 5
ACTUALS, BUDGET = 1, 2
AMOUNT, QUANTITY = 1, 2
BASE = 1


def connect() -> psycopg.Connection[Any]:
    return psycopg.connect(URL, autocommit=True)


def timed(fn: Callable[[], object], runs: int = 5) -> tuple[float, object]:
    """Median wall time in ms over ``runs`` after one warm-up, and the last result."""
    result = fn()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times), result


def report(rows: list[tuple[str, str]]) -> None:
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")
