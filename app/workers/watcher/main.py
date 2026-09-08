from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from prometheus_client import start_http_server

from app.libs.common.config import get_settings
from app.libs.common.logging_config import configure_logging, get_logger

from .jobs import scan_and_enqueue

settings = get_settings()
configure_logging(settings.log_level, service="watcher")
log = get_logger("watcher")

METRICS_PORT = 9101


async def _run_scan() -> None:
    try:
        await scan_and_enqueue()
    except Exception as exc:  # a failed scan must never kill the scheduler
        log.error("watcher_scan_failed", error=str(exc), exc_info=True)


async def main() -> None:
    if settings.prometheus_enabled:
        start_http_server(METRICS_PORT)
        log.info("watcher_metrics_listening", port=METRICS_PORT)

    interval = settings.poll_interval_seconds
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scan,
        "interval",
        seconds=interval,
        id="mbox_scan",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=interval,
    )
    scheduler.start()
    log.info("watcher_started", interval_seconds=interval, mbox_root=settings.mbox_root)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    await _run_scan()
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        log.info("watcher_stopped")


if __name__ == "__main__":
    asyncio.run(main())
