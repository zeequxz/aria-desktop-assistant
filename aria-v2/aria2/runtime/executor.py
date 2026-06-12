"""runtime/executor.py - Bounded worker pool for top-level runs.

Every surface used to spawn a raw daemon thread per run (chat send, trigger
fire, inbound message, fork). Under load that's unbounded thread + connection +
provider-request fan-out with no backpressure. This routes those *top-level*
runs through a single bounded pool so concurrency is capped and excess work
queues instead of exploding.

IMPORTANT: delegated sub-agent runs keep their OWN pool (delegation_tools), NOT
this one — otherwise a parent run holding a worker while it waits on its children
could starve them and deadlock the pool.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor


class RunExecutor:
    def __init__(self, max_workers: int = 8):
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_workers),
                                        thread_name_prefix="run")
        self._inflight = 0
        self._lock = threading.Lock()

    def submit(self, fn, *args, **kwargs) -> Future:
        with self._lock:
            self._inflight += 1

        def _wrapped():
            try:
                return fn(*args, **kwargs)
            finally:
                with self._lock:
                    self._inflight -= 1

        return self._pool.submit(_wrapped)

    def inflight(self) -> int:
        with self._lock:
            return self._inflight


_executor: RunExecutor | None = None
_init_lock = threading.Lock()


def _get() -> RunExecutor:
    global _executor
    if _executor is None:
        with _init_lock:
            if _executor is None:
                from aria2.core import config
                _executor = RunExecutor(
                    max_workers=int(config.get("max_concurrent_runs", 8) or 8))
    return _executor


def submit(fn, *args, **kwargs) -> Future:
    """Submit a top-level run callable to the bounded pool."""
    return _get().submit(fn, *args, **kwargs)


def inflight() -> int:
    """How many top-level runs are currently executing (for observability)."""
    return _get().inflight()
