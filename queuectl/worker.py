# queuectl/worker.py
import os
import time
import uuid
from multiprocessing import Process
from datetime import timedelta

from .storage import (
    fetch_and_lock_next_job,
    mark_completed,
    mark_failed_or_dead,
    register_worker,
    stop_worker_record,
    recover_processing,
    config_get,
    config_set,          # <-- needed for graceful shutdown on Ctrl+C
)
from .executor import run_command
from .utils import utcnow


def backoff_delay(base: float, attempts: int) -> float:
    """delay = base ** attempts (with safe casts)"""
    try:
        return float(base) ** int(attempts)
    except Exception:
        return 2.0 ** int(attempts)


def worker_loop(worker_id: str, poll_interval: float = 1.0):
    """
    Single worker process loop:
      - respects global 'shutdown' flag
      - fetches and locks one job at a time
      - runs command; on failure schedules retry with exponential backoff
      - always deregisters itself on exit
    """
    pid = os.getpid()
    register_worker(worker_id, pid)
    recover_processing()  # convert orphaned 'processing' jobs to 'failed' so they can be retried

    try:
        while True:
            if config_get("shutdown", "false") == "true":
                break

            job = fetch_and_lock_next_job(worker_id)
            if not job:
                time.sleep(poll_interval)
                continue

            rc, err = run_command(job["command"], timeout=None)

            if rc == 0:
                mark_completed(job["id"])
            else:
                attempts = int(job["attempts"]) + 1
                base = float(config_get("backoff_base", "2"))
                delay_sec = backoff_delay(base, attempts)
                next_run = utcnow() + timedelta(seconds=delay_sec)
                # truncate error to keep DB small
                mark_failed_or_dead(
                    job["id"],
                    attempts,
                    int(job["max_retries"]),
                    (err or "")[:512],
                    next_run,
                )
    except KeyboardInterrupt:
        # quiet exit on Ctrl+C
        pass
    finally:
        stop_worker_record(worker_id)


def start_workers(count: int):
    """
    Spawn N workers and join them. If Ctrl+C is pressed in the parent,
    set shutdown=true so children finish their current job and exit cleanly.
    """
    procs = []
    for _ in range(count):
        wid = f"w-{uuid.uuid4().hex[:8]}"
        p = Process(target=worker_loop, args=(wid,), daemon=False)
        p.start()
        procs.append(p)

    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        # parent interrupted -> request graceful stop for all workers
        config_set("shutdown", "true")
        for p in procs:
            p.join()
