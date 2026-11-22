"""Employer: the State-pattern client (see the Sprint 1 class diagram).

Each *_application method checks ownership first (diagram: private
verifyOwnership) and then delegates to the JobApplication, whose current
state decides whether the move is legal.
"""

from __future__ import annotations

from app.models.user import User
from app.services.application_states import IllegalTransitionError
from app.services.errors import ForbiddenError
from app.services.job_application import JobApplication


ACTION_ALIASES = {"mark_reviewed": "review", "hire": "accept"}
LIFECYCLE_ACTIONS = ("review", "shortlist", "accept", "reject")
UNSUPPORTED_ACTIONS = ("reopen",)  # no state allows it (FR4)


def canonical_action(action: str) -> str | None:
    action = ACTION_ALIASES.get(action, action)
    if action in LIFECYCLE_ACTIONS or action in UNSUPPORTED_ACTIONS:
        return action
    return None


class Employer:
    def __init__(self, user: User) -> None:
        self.user = user
        self.id = user.id

    def _verify_ownership(self, application: JobApplication) -> None:
        if application.listing.employer_id != self.id:
            raise ForbiddenError(
                "only the employer who owns this listing can change an "
                "application's status"
            )

    def review_application(self, application: JobApplication) -> None:
        self._verify_ownership(application)
        application.review(changed_by=self.user)

    def shortlist_application(self, application: JobApplication) -> None:
        self._verify_ownership(application)
        application.shortlist(changed_by=self.user)

    def accept_application(self, application: JobApplication) -> None:
        self._verify_ownership(application)
        application.accept(changed_by=self.user)

    def reject_application(
        self, application: JobApplication, reason: str | None = None
    ) -> None:
        self._verify_ownership(application)
        application.reject(reason, changed_by=self.user)

    def perform(
        self, action: str, application: JobApplication, reason: str | None = None
    ) -> None:
        """Dispatch an API action name to the matching method above."""
        if action in UNSUPPORTED_ACTIONS:
            self._verify_ownership(application)
            raise IllegalTransitionError(
                application.status, action, application.allowed_actions
            )
        if action == "reject":
            self.reject_application(application, reason)
            return
        {
            "review": self.review_application,
            "shortlist": self.shortlist_application,
            "accept": self.accept_application,
        }[action](application)
