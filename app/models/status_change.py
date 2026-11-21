from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StatusChange(SQLModel, table=True):

    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(index=True)
    old_status: str
    new_status: str
    actor_id: int
    changed_at: datetime = Field(default_factory=_utcnow)
