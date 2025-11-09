from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class Job(BaseModel):
    id: str
    command: str
    state: str = Field(default="pending")  # pending | processing | completed | failed | dead
    attempts: int = 0
    max_retries: int = 3
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime
    last_error: Optional[str] = None
    priority: int = 0
    worker_id: Optional[str] = None

DEFAULTS = {
    "max_retries": 3,
    "backoff_base": 2.0,
}
