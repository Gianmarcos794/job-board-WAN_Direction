from typing import Optional

from sqlmodel import Field, SQLModel


class Listing(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    employer_id: int = Field(index=True)
    title: str
    company: str
    description: str
    apply_mode: str
    external_url: Optional[str] = None
    deadline: str
    status: str
    # Admin's note when rejecting a pending listing (FR12); cleared on resubmit.
    rejection_reason: Optional[str] = None
