"""JobApplication: the State-pattern context (see the Sprint 1 class diagram).

Diagram -> Python naming: the diagram uses camelCase; this code uses the
snake_case equivalents, and getters are read-only properties
(getStatus() -> .status, getHistory() -> .history, getCandidate() ->
.candidate, getListing() -> .listing).
"""

from __future__ import annotations

from typing import Callable

from app.models.application import Application
from app.models.listing import Listing
from app.models.status_change import StatusChange
from app.models.user import User
from app.services.application_states import ApplicationState, state_from_status


class JobApplication:
    def __init__(
        self,
        record: Application,
        listing: Listing,
        candidate: User | None = None,
        history: list[StatusChange] | None = None,
    ) -> None:
        """Wrap a stored Application.

        ``history`` is the application's earlier StatusChange rows; changes
        made through this object are appended to it.
        """
        self._record = record
        self._listing = listing
        self._candidate = candidate
        self._state: ApplicationState = state_from_status(record.status)
        self._history: list[StatusChange] = list(history or [])
        self._saved_count = len(self._history)

    # --- getters (diagram: getStatus / getHistory / getCandidate / getListing)

    @property
    def status(self) -> str:
        return self._state.name

    @property
    def history(self) -> list[StatusChange]:
        return list(self._history)

    @property
    def candidate(self) -> User | None:
        return self._candidate

    @property
    def listing(self) -> Listing:
        return self._listing

    @property
    def rejection_reason(self) -> str | None:
        return self._record.rejection_reason

    @property
    def allowed_actions(self) -> list[str]:
        return self._state.allowed_actions()

    # --- called by the states only (diagram: ~setState, package-private)

    def set_state(self, state: ApplicationState) -> None:
        self._state = state
        self._record.status = state.name  # keep the stored column in step

    def set_rejection_reason(self, reason: str) -> None:
        self._record.rejection_reason = (reason or "").strip() or None

    # --- lifecycle actions: each delegates to the current state

    def review(self, changed_by: User | None = None) -> None:
        self._run(changed_by, lambda: self._state.review(self))

    def shortlist(self, changed_by: User | None = None) -> None:
        self._run(changed_by, lambda: self._state.shortlist(self))

    def accept(self, changed_by: User | None = None) -> None:
        self._run(changed_by, lambda: self._state.accept(self))

    def reject(self, reason: str | None = None, changed_by: User | None = None) -> None:
        self._run(changed_by, lambda: self._state.reject(self, reason or ""))

    # --- persistence support

    def unsaved_changes(self) -> list[StatusChange]:
        """History entries added since this object was built (to be saved)."""
        return self._history[self._saved_count :]

    def mark_saved(self) -> None:
        self._saved_count = len(self._history)

    def _run(self, changed_by: User | None, delegate: Callable[[], None]) -> None:
        old = self.status
        delegate()  # raises IllegalTransitionError if the state forbids it
        self._history.append(
            StatusChange(
                application_id=self._record.id,
                old_status=old,
                new_status=self.status,
                changed_by=changed_by.id if changed_by else None,
            )
        )
