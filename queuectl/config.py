from .storage import with_conn
from typing import Optional

@with_conn
def get_config(conn, key: str) -> Optional[str]:
    cur = conn.execute("SELECT value FROM config WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else None

@with_conn
def set_config(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
