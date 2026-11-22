from typing import Optional

from sqlmodel import Field, SQLModel


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    message: str
    kind: Optional[str] = None
    application_id: Optional[int] = None
