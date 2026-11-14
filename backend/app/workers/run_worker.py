"""CLI: python -m app.workers.run_worker [--once] [--concurrency N]"""
from __future__ import annotations

import argparse
import signal
import threading

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.worker import run_forever, run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="MedAudit background worker")
    parser.add_argument("--once", action="store_true", help="process at most one job and exit")
    parser.add_argument("--concurrency", type=int, default=None, help="worker threads (default WORKER_CONCURRENCY)")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    if args.once:
        job = run_once()
        print(f"processed job {job}" if job else "no queued jobs")
        return
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    n = args.concurrency or get_settings().worker_concurrency
    threads = [threading.Thread(target=run_forever, args=(stop,), daemon=True) for _ in range(max(1, n))]
    for t in threads:
        t.start()
    try:
        while not stop.is_set():
            stop.wait(1)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)


if __name__ == "__main__":
    main()
