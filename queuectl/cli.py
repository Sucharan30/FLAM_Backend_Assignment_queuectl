import json
from typing import Optional

import typer
from rich import print
from rich.table import Table
from rich.console import Console

from .utils import utcnow
from .storage import (
    upsert_job, list_jobs, counts_by_state, list_workers,
    config_get, config_set, get_job
)
from .worker import start_workers

app = typer.Typer(help="queuectl - background job queue with workers, retries and DLQ.")

# Sub-apps so CLI supports commands like:
#   queuectl worker start --count 3
#   queuectl config set max-retries 3
worker_app = typer.Typer()
config_app = typer.Typer()
dlq_app = typer.Typer()

app.add_typer(worker_app, name="worker")
app.add_typer(config_app, name="config")
app.add_typer(dlq_app, name="dlq")


# -----------------------------
# Enqueue (PowerShell-friendly)
# -----------------------------
@app.command()
def enqueue(
    payload: Optional[str] = typer.Argument(
        None,
        help=(
            "Job JSON e.g. '{\"id\":\"job1\",\"command\":\"echo hi\"}'. "
            "Optional if you use --id and --command or --json-file."
        ),
    ),
    id: Optional[str] = typer.Option(None, "--id", help="Job ID (use with --command)"),
    command: Optional[str] = typer.Option(None, "--command", help="Command to run (use with --id)"),
    json_file: Optional[str] = typer.Option(None, "--json-file", help="Read JSON payload from a file"),
    max_retries: Optional[int] = typer.Option(None, help="Override job max_retries"),
    priority: int = typer.Option(0, help="Optional priority (higher first)"),
):
    """Add a new job to the queue."""
    # Load payload from file if provided
    if json_file:
        from pathlib import Path
        payload = Path(json_file).read_text(encoding="utf-8").strip()

    # Accept either JSON payload OR --id + --command
    if payload:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise typer.BadParameter(f"Invalid JSON: {e.msg}")
    elif id and command:
        data = {"id": id, "command": command}
    else:
        raise typer.BadParameter("Provide JSON payload OR both --id and --command (or use --json-file).")

    now = utcnow()
    job = {
        "id": data["id"],
        "command": data["command"],
        "state": "pending",
        "attempts": 0,
        "max_retries": max_retries if max_retries is not None else int(config_get("max_retries", "3")),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "next_run_at": now.isoformat(),
        "last_error": None,
        "priority": priority,
        "worker_id": None,
    }
    upsert_job(job)
    print(f"[green]Enqueued[/green] job [bold]{job['id']}[/bold]")


# -----------------------------
# Worker controls (legacy style)
# -----------------------------
@app.command("worker")
def worker_start(
    count: int = typer.Option(1, "--count", "-c", help="Number of worker processes"),
    reset_shutdown: bool = typer.Option(True, help="Set shutdown=false before start"),
):
    """Start worker processes."""
    if reset_shutdown:
        config_set("shutdown", "false")
    print(f"Starting {count} worker(s). Ctrl+C to stop.")
    start_workers(count)


@app.command("stop")
def worker_stop():
    """Signal workers to stop gracefully (finish current job)."""
    config_set("shutdown", "true")
    print("[yellow]Set shutdown=true. Workers will exit after finishing the current job.[/yellow]")


# worker subcommands to match assignment examples
@worker_app.command("start")
def worker_start_cmd(
    count: int = typer.Option(1, "--count", "-c", help="Number of worker processes"),
    reset_shutdown: bool = typer.Option(True, help="Set shutdown=false before start"),
):
    """Start worker processes (alias for `queuectl worker`)."""
    if reset_shutdown:
        config_set("shutdown", "false")
    print(f"Starting {count} worker(s). Ctrl+C to stop.")
    start_workers(count)


@worker_app.command("stop")
def worker_stop_cmd():
    """Stop running workers gracefully (alias for `queuectl stop`)."""
    config_set("shutdown", "true")
    print("[yellow]Set shutdown=true. Workers will exit after finishing the current job.[/yellow]")


# -----------------------------
# Status & listing
# -----------------------------
@app.command()
def status():
    """Show job state counts and active workers."""
    console = Console()
    tbl = Table(title="Jobs")
    tbl.add_column("State")
    tbl.add_column("Count")
    for row in counts_by_state():
        tbl.add_row(str(row[0]), str(row[1]))
    console.print(tbl)

    wt = Table(title="Active Workers")
    wt.add_column("worker_id")
    wt.add_column("pid")
    wt.add_column("started_at")
    for w in list_workers():
        wt.add_row(w["id"], str(w["pid"]), w["started_at"])
    console.print(wt)


@app.command()
def list(state: Optional[str] = typer.Option(None, "--state", help="Filter by state")):
    """List jobs, optionally by state."""
    rows = list_jobs(state)
    t = Table(title=f"Jobs{'' if not state else f' ({state})'}")
    for c in ["id", "state", "attempts", "max_retries", "priority", "next_run_at", "updated_at", "command"]:
        t.add_column(c)
    for r in rows:
        t.add_row(
            r["id"],
            r["state"],
            str(r["attempts"]),
            str(r["max_retries"]),
            str(r["priority"]),
            r["next_run_at"],
            r["updated_at"],
            r["command"],
        )
    Console().print(t)


# -----------------------------
# DLQ (list + retry)
# -----------------------------
@app.command("dlq")
def dlq_list():
    """List Dead Letter Queue jobs."""
    rows = list_jobs("dead")
    t = Table(title="DLQ (dead jobs)")
    t.add_column("id")
    t.add_column("attempts")
    t.add_column("max_retries")
    t.add_column("last_error")
    for r in rows:
        t.add_row(r["id"], str(r["attempts"]), str(r["max_retries"]), (r["last_error"] or "")[:80])
    Console().print(t)


@app.command("retry")
def dlq_retry(job_id: str):
    """Retry a DLQ job: reset state and attempts."""
    row = get_job(job_id)
    if not row or row["state"] != "dead":
        print(f"[red]Not found in DLQ:[/red] {job_id}")
        raise typer.Exit(1)
    now = utcnow().isoformat()
    payload = dict(row)
    payload.update(
        {
            "state": "pending",
            "attempts": 0,
            "last_error": None,
            "next_run_at": now,
            "updated_at": now,
            "worker_id": None,
        }
    )
    upsert_job(payload)
    print(f"[green]DLQ job re-queued:[/green] {job_id}")


# dlq subcommands
@dlq_app.command("list")
def dlq_list_cmd():
    """List dead jobs (alias)."""
    return dlq_list()


@dlq_app.command("retry")
def dlq_retry_cmd(job_id: str):
    """Retry a DLQ job (alias)."""
    return dlq_retry(job_id)


# -----------------------------
# Config (get/set switch)
# -----------------------------
@app.command("config")
def config_cmd(
    get: Optional[str] = typer.Option(None, "--get", help="Read config key"),
    set: Optional[str] = typer.Option(None, "--set", help="Write config key"),
    value: Optional[str] = typer.Option(None, "--value", help="Value with --set"),
):
    """
    Manage configuration keys. Keys used: max_retries, backoff_base, shutdown.
    """
    if get:
        print(config_get(get, ""))
        return
    if set is not None and value is not None:
        config_set(set, value)
        print(f"set {set}={value}")
        return
    raise typer.Exit(code=2)


# config subcommands to match examples: `queuectl config set max-retries 3`
@config_app.command("set")
def config_set_cmd(key: str = typer.Argument(..., help="Config key"), value: str = typer.Argument(..., help="Value")):
    config_set(key, value)
    print(f"set {key}={value}")


@config_app.command("get")
def config_get_cmd(key: str = typer.Argument(..., help="Config key")):
    print(config_get(key, ""))


# --------------------------------------------------------------------
# Convenience ALIASES (no breaking changes to your existing commands)
# --------------------------------------------------------------------
@app.command("worker-start")
def worker_start_alias(
    count: int = typer.Option(1, "--count", "-c", help="Number of worker processes"),
    reset_shutdown: bool = typer.Option(True, help="Set shutdown=false before start"),
):
    """Alias for: queuectl worker --count N"""
    worker_start(count=count, reset_shutdown=reset_shutdown)


@app.command("worker-stop")
def worker_stop_alias():
    """Alias for: queuectl stop"""
    worker_stop()


@app.command("dlq-list")
def dlq_list_alias():
    """Alias for: queuectl dlq"""
    dlq_list()


@app.command("dlq-retry")
def dlq_retry_alias(job_id: str):
    """Alias for: queuectl retry <job_id>"""
    dlq_retry(job_id)


@app.command("config-get")
def config_get_alias(key: str):
    """Alias for: queuectl config --get <key>"""
    print(config_get(key, ""))


@app.command("config-set")
def config_set_alias(key: str, value: str):
    """Alias for: queuectl config --set <key> --value <value>"""
    config_set(key, value)
    print(f"set {key}={value}")
