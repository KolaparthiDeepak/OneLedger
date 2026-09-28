"""Local/container worker: runs the same scheduler tick as the hosted runner endpoint, in a loop."""

from __future__ import annotations

import logging
import signal
import time

from oneledger_shared.logging import configure_logging

from . import services  # noqa: F401
from .context import get_context
from .services import ai, analytics_jobs, imports, scheduler  # noqa: F401  (register job handlers)
from .services.jobs import run_until

log = logging.getLogger("oneledger.worker")
_stop = False


def _handle(signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def main() -> None:
    ctx = get_context()
    configure_logging(ctx.settings.log_level)
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    log.info("worker started", extra={"event": "worker_start"})
    last_plan = 0.0
    while not _stop:
        try:
            if time.monotonic() - last_plan > 60:
                scheduler.plan(ctx)
                last_plan = time.monotonic()
            processed = run_until(ctx, ctx.settings.job_time_budget_seconds)
        except Exception:
            log.exception("worker tick failed", extra={"event": "worker_error"})
            processed = 0
        if not processed:
            for _ in range(int(ctx.settings.worker_poll_seconds * 10)):
                if _stop:
                    break
                time.sleep(0.1)
    log.info("worker stopped", extra={"event": "worker_stop"})


if __name__ == "__main__":
    main()
