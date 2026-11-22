from typing import Optional

from sqlmodel import Field, SQLModel


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    listing_id: int = Field(index=True)
    graduate_id: int = Field(index=True)
    cover_letter: str
    resume_text: str
    # Single source of truth for the lifecycle; driven by the State objects in
    # app/services/application_states.py via JobApplication.
    status: str
    rejection_reason: Optional[str] = None
