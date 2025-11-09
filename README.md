# queuectl — CLI Background Job Queue (Python + SQLite)

`queuectl` is a small, production-style background job queue you run from the command line.  
It enqueues shell commands as jobs, runs them in **multi-process workers**, retries failures with **exponential backoff**, and moves permanently failing jobs to a **Dead Letter Queue (DLQ)**. Jobs and config are persisted in **SQLite**, so your state survives restarts.

---

## ✨ Features

- Enqueue jobs via **JSON payload** or **flags** (`--id`, `--command`) — Windows-friendly.
- **Multiple workers** using Python `multiprocessing`.
- **Exponential backoff** retries (`delay = base ** attempts`) with configurable `backoff_base`.
- **Dead Letter Queue (DLQ)** after `max_retries`.
- **Persistent** storage with SQLite:
  - Windows: `%USERPROFILE%\.queuectl\queue.db`
  - macOS/Linux: `~/.queuectl/queue.db`
  - Override base dir with `QUEUECTL_HOME`
- **Graceful shutdown** using a `shutdown` config flag (finish current job, then exit).
- Clean CLI with **Rich** tables and **Typer** command structure.

---

## 🚀 Setup Instructions

### Prerequisites
- Python 3.10+
- Windows PowerShell or a Unix shell

### Create venv & install
```bash
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt



### Quick sanity check

```bash
python -m queuectl --help
```

---

## 🧪 Usage Examples

> The CLI supports both **grouped** subcommands (spec-style) and **aliases**.
> On Windows, prefer `--id/--command` to avoid JSON quoting issues.

### Start workers

```bash
python -m queuectl worker start --count 2
# alias: python -m queuectl worker-start --count 2
```

### Enqueue jobs

```bash
# Windows-safe flags
python -m queuectl enqueue --id ok1 --command "cmd /c echo hello"

# JSON payload (quote carefully on Windows)
python -m queuectl enqueue "{\"id\":\"job2\",\"command\":\"echo hi\"}"
```

### Status and list

```bash
python -m queuectl status
python -m queuectl list --state pending
python -m queuectl list --state processing
python -m queuectl list --state completed
```

**Example output (truncated):**

```
Jobs
State       Count
----------  -----
pending     1
processing  2
completed   3
dead        0

Active Workers
worker_id   pid    started_at
----------  -----  --------------------------
w-73e75aa3  28852  2025-11-09T07:03:58.068218
w-9d7bd223  31908  2025-11-09T07:03:58.080233
```

### Retries, backoff and DLQ

```bash
python -m queuectl config set max_retries 2
python -m queuectl config set backoff_base 2

# guaranteed failure
python -m queuectl enqueue --id bad1 --command "idontexist_123"

# after a few seconds (backoff), it will land in DLQ
python -m queuectl dlq list

# retry from DLQ
python -m queuectl dlq retry bad1
```

### Graceful stop

```bash
python -m queuectl worker stop
# alias: python -m queuectl worker-stop
```

Workers finish their current job and exit. Start again with `worker start`.

---

## 🎥 Demo Video

Watch the end-to-end CLI demo here:
**[queuectl CLI Demo (Google Drive)](https://drive.google.com/file/d/1UycqAydFvHPEIxmtZmhdo_frF1iadADe/view?usp=sharing)**

You can also run the scripted demo on Windows:

```bash
powershell -ExecutionPolicy Bypass -File .\tests\demo.ps1
```

---

## 🧱 Architecture Overview

* **CLI (`cli.py`)** — commands: `enqueue`, `worker start/stop`, `status`, `list`, `dlq list/retry`, `config get/set`.
* **Storage (`storage.py`)** — SQLite schema, atomic fetch-and-lock, job updates, config K/V, worker registry.
* **Worker (`worker.py`)** — multi-process loop, crash recovery, exponential backoff scheduling, graceful shutdown (Ctrl+C sets `shutdown=true` and joins).
* **Executor (`executor.py`)** — runs shell commands, returns `(exit_code, error_text)`.
* **Utils (`utils.py`)** — UTC timestamps, app data dir, ISO formatting.

**Job lifecycle**

```
pending --(claim)--> processing --(exit code 0)--> completed
                           |
                           | (exit code != 0)
                           v
                        failed --(attempts < max_retries)--> pending (after backoff)
                           |
                           | (attempts >= max_retries)
                           v
                          dead (DLQ)
```

**Locking**

* Claim is transactional: select one eligible job, set `processing` + `worker_id` in the same transaction → no duplicate execution.

> See **[DESIGN.md](DESIGN.md)** for schema, locking algorithm, state machine, recovery, and extensions.

---

## ⚖️ Assumptions & Trade-offs

* **SQLite** chosen for simplicity and ACID on a single host; not a distributed/high-throughput queue.
* **shell=True** is convenient for an assignment; only run trusted commands.
* **At-least-once** semantics: a job may be retried if a worker crashes mid-exec.
* Minimal baseline: no per-job timeout or `run_at` scheduling in the core flow (both are simple extensions).

---

## ✅ Testing Instructions

### Automated demo (Windows)

```bash
powershell -ExecutionPolicy Bypass -File .\tests\demo.ps1
```

Shows:

* Workers start
* Success case
* Parallelism (no overlap with multiple workers)
* Retry → backoff → DLQ
* Retry from DLQ
* Graceful stop

### Manual checks

1. `python -m queuectl worker start --count 2`
2. `python -m queuectl enqueue --id ok --command "cmd /c echo hello"`
3. Enqueue several long jobs (`timeout /T 8`) and run `list --state processing` to see parallelism.
4. Enqueue `idontexist_123`, wait, then `dlq list`.
5. Stop workers, restart them, confirm **persistence** of job history.

---

## 🗂️ Project Layout

```
queuectl/
  __init__.py
  cli.py          # Typer CLI (enqueue, worker, status, list, dlq, config)
  storage.py      # SQLite access, transactions, job locking, config
  worker.py       # worker loop, backoff, graceful shutdown, recovery
  executor.py     # subprocess execution wrapper
  utils.py        # time & path helpers
tests/
  demo.ps1        # end-to-end Windows demo script
README.md
DESIGN.md
requirements.txt
```

---

## 🆘 Troubleshooting

* Nothing runs after `worker stop` → `python -m queuectl config set shutdown false` then `worker start`.
* Windows JSON quoting errors → prefer `--id/--command`.
* Fresh start → stop workers, delete DB at `%USERPROFILE%\.queuectl\queue.db`, then `worker start`.







