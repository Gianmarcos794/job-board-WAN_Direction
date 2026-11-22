from typing import Optional

from sqlmodel import Field, SQLModel


class NotificationPreference(SQLModel, table=True):
    """Whether a user wants email for one kind of notification (FR22).

    No row means the default: email on. In-app notifications are always kept.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    kind: str
    email: bool = True
