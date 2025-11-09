# DESIGN — queuectl

This document explains how `queuectl` manages jobs, locking, retries, shutdown, and recovery.
Goal: a small, production-style CLI queue with **multi-process workers**, **exponential backoff**, **DLQ**, and **durable state** in **SQLite**.

---

## 1) Components

* **CLI (`cli.py`)** — user commands: `enqueue`, `worker start/stop`, `status`, `list`, `dlq list/retry`, `config get/set`.
* **Storage (`storage.py`)** — SQLite access, schema creation/migrations, atomic fetch-and-lock, job state updates, config K/V.
* **Worker (`worker.py`)** — worker loop, crash recovery, exponential backoff scheduling, graceful shutdown, Ctrl+C handling.
* **Executor (`executor.py`)** — runs shell commands and returns `(exit_code, error_text)`.
* **Utils (`utils.py`)** — UTC time helpers, app data path resolution, ISO-8601 formatting.

---

## 2) Data Model

### 2.1 Jobs table

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  command     TEXT NOT NULL,
  state       TEXT NOT NULL,            -- pending | processing | completed | failed | dead
  attempts    INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL,
  priority    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,            -- ISO-8601 UTC
  updated_at  TEXT NOT NULL,
  next_run_at TEXT NOT NULL,
  last_error  TEXT,
  worker_id   TEXT
);

-- Efficient lookup for runnable jobs; selection also orders by priority.
CREATE INDEX IF NOT EXISTS idx_jobs_state_next ON jobs(state, next_run_at);
```

### 2.2 Workers table (for status display)

```sql
CREATE TABLE IF NOT EXISTS workers (
  id         TEXT PRIMARY KEY,
  pid        INTEGER NOT NULL,
  started_at TEXT NOT NULL
);
```

### 2.3 Config table (key/value)

```sql
CREATE TABLE IF NOT EXISTS config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
/* keys used: max_retries, backoff_base, shutdown */
```

**Default DB location**

* Windows: `%USERPROFILE%\.queuectl\queue.db`
* macOS/Linux: `~/.queuectl/queue.db`
  Override base directory with env var **`QUEUECTL_HOME`**.

---

## 3) Job Lifecycle (State Machine)

```
pending --(worker claims)--> processing --(exit code 0)--> completed
                                  |
                                  | (exit code != 0)
                                  v
                               failed --(attempts < max_retries)--> pending (after backoff)
                                  |
                                  | (attempts >= max_retries)
                                  v
                                 dead (DLQ)
```

* Backoff formula: **`delay_seconds = backoff_base ** attempts`** (configurable; default `2`).
* Jobs carry an optional **priority** (higher first) and **next_run_at** for scheduling.

---

## 4) Atomic Claiming (preventing duplicate work)

Workers claim jobs **inside one transaction** so only one process can move a job to `processing`.

**Algorithm (simplified):**

1. `BEGIN IMMEDIATE;`
2. `SELECT id FROM jobs WHERE state IN ('pending','failed') AND next_run_at <= ? ORDER BY priority DESC, next_run_at ASC, created_at ASC LIMIT 1;`
3. If a row exists →
   `UPDATE jobs SET state='processing', worker_id=?, updated_at=? WHERE id=?;`
4. `COMMIT;`

If no row is eligible, the worker sleeps briefly and retries.
This yields **at-least-once** semantics while avoiding duplicate claims across processes.

---

## 5) Worker Loop

Pseudocode mirroring the implementation:

```python
register_worker(worker_id, pid)
recover_processing()  # convert orphaned 'processing' to 'failed' so they retry

while config_get('shutdown', 'false') != 'true':
    job = fetch_and_lock_next_job(worker_id)  # atomic claim
    if not job:
        sleep(poll_interval)
        continue

    rc, err = run_command(job['command'], timeout=None)

    if rc == 0:
        mark_completed(job['id'])
    else:
        attempts = int(job['attempts']) + 1
        base = float(config_get('backoff_base', '2'))
        delay = base ** attempts
        next_run_at = utcnow() + timedelta(seconds=delay)
        mark_failed_or_dead(
            job_id=job['id'],
            attempts=attempts,
            max_retries=int(job['max_retries']),
            last_error=(err or '')[:512],
            next_run_at=next_run_at
        )

# always:
stop_worker_record(worker_id)
```

The parent spawns N processes (`start_workers`). On **Ctrl+C** in the parent, it sets `shutdown=true` and joins children so they finish their current job and exit cleanly.

---

## 6) Retry & Backoff

* On failure: increment `attempts`, compute new `next_run_at` with `backoff_base ** attempts`.
* If `attempts >= max_retries`: transition to `dead` (DLQ).
* Configurable keys (via CLI): `max_retries`, `backoff_base`.

---

## 7) Crash Recovery

* `recover_processing()` runs at worker startup and flips any **orphaned `processing`** jobs to `failed` with `next_run_at = now`, so they re-enter the retry loop.
* Because claiming stores a `worker_id` in the same transaction, orphaned claims are detectable and correctable.

---

## 8) Graceful Shutdown

* Global config key **`shutdown`** controls termination.
* `queuectl worker stop` (or parent Ctrl+C) → set `shutdown=true`.
  Workers finish their current task and exit. New work is not claimed while the flag is true.

---

## 9) Concurrency Guarantees

* **At-least-once** execution: a job may be retried if a worker crashes during execution.
* **No duplicate claim**: atomic fetch-and-lock prevents two workers from setting `processing` on the same job.
* **Fair scheduling**: `(priority DESC, next_run_at ASC, created_at ASC)`.

---

## 10) Platform Notes

* Commands execute via system shell (`subprocess.run(shell=True)`).

  * Windows: use `cmd /c ...` or `timeout /T ...`.
  * Unix: use `sleep`, `echo`, etc.
* To avoid PowerShell quoting issues, the CLI supports `--id` and `--command` flags instead of JSON payloads.

---

## 11) Assumptions & Trade-offs

* **SQLite** chosen for simplicity and ACID guarantees on a single host. Not intended for high-throughput distributed use.
* **shell=True** is convenient for the assignment; only trusted inputs should be executed.
* Minimal scope: no per-job hard timeout or scheduled `run_at` field in the base flow (both are straightforward extensions).

---

## 12) Extensibility (Future Work)

* Per-job **timeout** and termination handling in `executor.py`.
* Explicit **`run_at`** field for scheduled/deferred jobs.
* **Priority lanes** / named queues.
* Persistent **stdout/stderr logs** under `~/.queuectl/logs/<job_id>.log`.
* **Metrics** and a small web dashboard for monitoring.
* Idempotency keys to support **exactly-once** behavior at the application layer.

```
```
