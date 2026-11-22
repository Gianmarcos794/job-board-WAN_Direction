"""
Application lifecycle states (State pattern).

Each status an application can be in is represented by one state class.
A state only overrides the actions that are legal from that status; every
other action falls through to the base class and raises
``IllegalTransitionError``.

Lifecycle::

    submitted --review--> reviewed --shortlist--> shortlisted --accept--> hired
                              |                        |
                              +--------reject----------+-----> rejected
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class ApplicationContext(Protocol):
    """What a state needs from the JobApplication context."""

    def set_state(self, state: "ApplicationState") -> None: ...

    def set_rejection_reason(self, reason: str) -> None: ...


ACTIONS = ("review", "shortlist", "accept", "reject")


class IllegalTransitionError(Exception):
    """Raised when an action is not allowed from the current status."""

    def __init__(self, status: str, action: str, allowed: list[str]) -> None:
        self.status = status
        self.action = action
        self.allowed = list(allowed)
        allowed_text = ", ".join(self.allowed) if self.allowed else "none"
        super().__init__(
            f"Cannot {action!r} an application with status {status!r}; "
            f"allowed actions: {allowed_text}"
        )


class ApplicationState(ABC):
    """Base state: every action is illegal unless a subclass overrides it.

    Abstract: each concrete state must set ``name`` (its stored status).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The status string stored on the Application row."""

    def allowed_actions(self) -> list[str]:
        """Actions this state overrides (i.e. the legal moves)."""
        return [
            action
            for action in ACTIONS
            if getattr(type(self), action) is not getattr(ApplicationState, action)
        ]

    def _illegal(self, action: str) -> IllegalTransitionError:
        return IllegalTransitionError(self.name, action, self.allowed_actions())

    def review(self, app: ApplicationContext) -> None:
        raise self._illegal("review")

    def shortlist(self, app: ApplicationContext) -> None:
        raise self._illegal("shortlist")

    def accept(self, app: ApplicationContext) -> None:
        raise self._illegal("accept")

    def reject(self, app: ApplicationContext, reason: str) -> None:
        raise self._illegal("reject")

    @property
    def is_final(self) -> bool:
        return not self.allowed_actions()

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def __eq__(self, other: Any) -> bool:
        return type(self) is type(other)

    def __hash__(self) -> int:
        return hash(type(self))


class SubmittedState(ApplicationState):
    name = "submitted"

    def review(self, app: ApplicationContext) -> None:
        app.set_state(UnderReviewState())


class UnderReviewState(ApplicationState):
    name = "reviewed"

    def shortlist(self, app: ApplicationContext) -> None:
        app.set_state(ShortlistedState())

    def reject(self, app: ApplicationContext, reason: str) -> None:
        app.set_rejection_reason(reason)
        app.set_state(RejectedState())


class ShortlistedState(ApplicationState):
    name = "shortlisted"

    def accept(self, app: ApplicationContext) -> None:
        app.set_state(HireState())

    def reject(self, app: ApplicationContext, reason: str) -> None:
        app.set_rejection_reason(reason)
        app.set_state(RejectedState())


class HireState(ApplicationState):
    """Final state: no legal moves."""

    name = "hired"


class RejectedState(ApplicationState):
    """Final state: no legal moves."""

    name = "rejected"


STATE_REGISTRY: dict[str, type[ApplicationState]] = {
    cls.name: cls
    for cls in (
        SubmittedState,
        UnderReviewState,
        ShortlistedState,
        HireState,
        RejectedState,
    )
}


def state_from_status(status: str) -> ApplicationState:
    """Return a fresh state object for a stored status string."""
    try:
        return STATE_REGISTRY[status]()
    except KeyError:
        raise ValueError(
            f"Unknown application status {status!r}; "
            f"expected one of {sorted(STATE_REGISTRY)}"
        ) from None
