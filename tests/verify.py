import sqlite3
from pathlib import Path
import os

DB_DIR = Path(os.environ.get("QUEUECTL_HOME", Path.home() / ".queuectl"))
DB_PATH = DB_DIR / "queue.db"

if not DB_PATH.exists():
    print("No DB found at", DB_PATH)
    raise SystemExit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("DB:", DB_PATH)
for row in cur.execute("SELECT state, COUNT(*) as c FROM jobs GROUP BY state"):
    print(row["state"], row["c"]) 

print("Workers active:")
for w in cur.execute("SELECT id,pid,started_at,stopped_at FROM workers"):
    print(w["id"], w["pid"], w["started_at"], w["stopped_at"])

conn.close()
