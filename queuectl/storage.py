import sqlite3, os, threading
from functools import wraps
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from .utils import utcnow
from .models import DEFAULTS

_DB_DIR = Path(os.environ.get("QUEUECTL_HOME", Path.home() / ".queuectl"))
_DB_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DB_DIR / "queue.db"
_local = threading.local()

def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
        init_db(conn)
    return conn

def with_conn(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        conn = get_conn()
        return fn(conn, *args, **kwargs)
    return wrapper

def init_db(conn: sqlite3.Connection):
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY,
          command TEXT NOT NULL,
          state TEXT NOT NULL,
          attempts INTEGER NOT NULL,
          max_retries INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          next_run_at TEXT NOT NULL,
          last_error TEXT,
          priority INTEGER NOT NULL DEFAULT 0,
          worker_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_state_next ON jobs(state,next_run_at);
        CREATE TABLE IF NOT EXISTS config(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workers(
          id TEXT PRIMARY KEY,
          pid INTEGER NOT NULL,
          started_at TEXT NOT NULL,
          stopped_at TEXT
        );
        """
    )
    # defaults
    for k,v in DEFAULTS.items():
        conn.execute(
            "INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
            (k, str(v)),
        )
    conn.execute("INSERT INTO config(key,value) VALUES('shutdown','false') ON CONFLICT(key) DO NOTHING")
    conn.commit()

@with_conn
def upsert_job(conn, job: Dict[str, Any]):
    conn.execute(
        """INSERT INTO jobs(id,command,state,attempts,max_retries,created_at,updated_at,next_run_at,last_error,priority,worker_id)
           VALUES(:id,:command,:state,:attempts,:max_retries,:created_at,:updated_at,:next_run_at,:last_error,:priority,:worker_id)
           ON CONFLICT(id) DO UPDATE SET
             command=excluded.command,
             state=excluded.state,
             attempts=excluded.attempts,
             max_retries=excluded.max_retries,
             updated_at=excluded.updated_at,
             next_run_at=excluded.next_run_at,
             last_error=excluded.last_error,
             priority=excluded.priority,
             worker_id=excluded.worker_id
        """, job)
    conn.commit()

@with_conn
def get_job(conn, job_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
    return cur.fetchone()

@with_conn
def list_jobs(conn, state: Optional[str] = None) -> List[sqlite3.Row]:
    if state:
        cur = conn.execute("SELECT * FROM jobs WHERE state=? ORDER BY created_at", (state,))
    else:
        cur = conn.execute("SELECT * FROM jobs ORDER BY created_at")
    return cur.fetchall()

@with_conn
def counts_by_state(conn) -> List[Tuple[str,int]]:
    cur = conn.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state")
    return cur.fetchall()

@with_conn
def config_get(conn, key: str, default: Optional[str]=None) -> str:
    cur = conn.execute("SELECT value FROM config WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default

@with_conn
def config_set(conn, key: str, value: str):
    conn.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()

@with_conn
def recover_processing(conn):
    now = utcnow().isoformat()
    conn.execute("""
      UPDATE jobs
         SET state='failed', next_run_at=?, worker_id=NULL, updated_at=?
       WHERE state='processing'
    """, (now, now))
    conn.commit()

@with_conn
def fetch_and_lock_next_job(conn, worker_id: str) -> Optional[sqlite3.Row]:
    """BEGIN IMMEDIATE ensures only one writer wins; we move a job to processing atomically."""
    now = utcnow().isoformat()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """
        SELECT id FROM jobs
         WHERE state IN ('pending','failed') AND next_run_at <= ?
         ORDER BY priority DESC, next_run_at ASC, created_at ASC
         LIMIT 1
        """, (now,)
    ).fetchone()
    if not row:
        conn.execute("COMMIT")
        return None
    job_id = row["id"]
    conn.execute(
        "UPDATE jobs SET state='processing', worker_id=?, updated_at=? WHERE id=?",
        (worker_id, now, job_id),
    )
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.execute("COMMIT")
    return job

@with_conn
def mark_completed(conn, job_id: str):
    now = utcnow().isoformat()
    conn.execute("UPDATE jobs SET state='completed', updated_at=?, worker_id=NULL WHERE id=?", (now, job_id))
    conn.commit()

@with_conn
def mark_failed_or_dead(conn, job_id: str, attempts: int, max_retries: int, last_error: str, next_run_at: Optional[datetime]):
    now = utcnow().isoformat()
    # Move to dead when attempts >= max_retries so a job with max_retries=3
    # will be attempted up to 3 times and be marked dead on the 3rd failing attempt.
    if attempts >= max_retries:
        conn.execute(
            "UPDATE jobs SET state='dead', attempts=?, last_error=?, updated_at=?, worker_id=NULL WHERE id=?",
            (attempts, last_error, now, job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET state='failed', attempts=?, last_error=?, next_run_at=?, updated_at=?, worker_id=NULL WHERE id=?",
            (attempts, last_error, next_run_at.isoformat(), now, job_id),
        )
    conn.commit()

@with_conn
def register_worker(conn, wid: str, pid: int):
    conn.execute("INSERT INTO workers(id,pid,started_at) VALUES(?,?,?)", (wid, pid, utcnow().isoformat()))
    conn.commit()

@with_conn
def stop_worker_record(conn, wid: str):
    conn.execute("UPDATE workers SET stopped_at=? WHERE id=?", (utcnow().isoformat(), wid))
    conn.commit()

@with_conn
def list_workers(conn):
    return conn.execute("SELECT * FROM workers WHERE stopped_at IS NULL").fetchall()
