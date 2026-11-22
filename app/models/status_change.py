from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StatusChange(SQLModel, table=True):
    """One history record per lifecycle transition (FR6).

    Diagram: oldStatus, newStatus, changedBy (stored as the user's id), changedAt.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(index=True)
    old_status: str
    new_status: str
    changed_by: Optional[int] = Field(default=None, index=True)  # user id
    changed_at: datetime = Field(default_factory=_utcnow)
