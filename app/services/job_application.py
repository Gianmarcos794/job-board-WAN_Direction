from __future__ import annotations

from app.models.application import Application
from app.models.status_change import StatusChange
from app.services.application_states import (
    ApplicationState,
    IllegalTransitionError,
    state_from_status,
)


class JobApplication:
    def __init__(self, record: Application, employer_id: int) -> None:
        self._record = record
        self.employer_id = employer_id  
        self._state: ApplicationState = state_from_status(record.status)
        self._changes: list[StatusChange] = []


    @property
    def id(self) -> int | None:
        return self._record.id

    @property
    def record(self) -> Application:
        return self._record

    @property
    def state(self) -> ApplicationState:
        return self._state

    @property
    def status(self) -> str:
        return self._state.name

    @property
    def allowed_actions(self) -> list[str]:
        return self._state.allowed_actions()


    def set_state(self, state: ApplicationState) -> None:
        self._state = state
        self._record.status = state.name  # keep the stored column in step

    def set_rejection_reason(self, reason: str) -> None:
        self._record.rejection_reason = (reason or "").strip() or None


    def review(self, actor_id: int) -> None:
        self._run(actor_id, lambda: self._state.review(self))

    def shortlist(self, actor_id: int) -> None:
        self._run(actor_id, lambda: self._state.shortlist(self))

    def accept(self, actor_id: int) -> None:
        self._run(actor_id, lambda: self._state.accept(self))

    def reject(self, actor_id: int, reason: str | None = None) -> None:
        self._run(actor_id, lambda: self._state.reject(self, reason or ""))

    def refuse(self, action: str) -> None:
        """An action that exists in no state (e.g. ``reopen``): always refused."""
        raise IllegalTransitionError(self.status, action, self.allowed_actions)


    def drain_changes(self) -> list[StatusChange]:
        changes, self._changes = self._changes, []
        return changes

    def _run(self, actor_id: int, delegate) -> None:
        old = self.status
        delegate() 
        self._changes.append(
            StatusChange(
                application_id=self._record.id,
                old_status=old,
                new_status=self.status,
                actor_id=actor_id,
            )
        )
