"""Small precondition checks shared by services."""

from __future__ import annotations

from app.models.user import User
from app.repositories.board import BoardUserRepository
from app.services.errors import NotFoundError


def require_user(users: BoardUserRepository, user_id: int) -> User:
    user = users.get(user_id)
    if user is None:
        raise NotFoundError("unknown user")
    return user
