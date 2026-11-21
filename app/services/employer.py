
from __future__ import annotations

from app.models.user import User
from app.services.errors import ForbiddenError
from app.services.job_application import JobApplication


ACTION_ALIASES = {"mark_reviewed": "review", "hire": "accept"}
LIFECYCLE_ACTIONS = ("review", "shortlist", "accept", "reject")
UNSUPPORTED_ACTIONS = ("reopen",)


def canonical_action(action: str) -> str | None:
    action = ACTION_ALIASES.get(action, action)
    if action in LIFECYCLE_ACTIONS or action in UNSUPPORTED_ACTIONS:
        return action
    return None


class Employer:
    def __init__(self, user: User) -> None:
        self.id = user.id

    def verify_ownership(self, application: JobApplication) -> None:
        if application.employer_id != self.id:
            raise ForbiddenError(
                "only the employer who owns this listing can change an "
                "application's status"
            )

    def review_application(self, application: JobApplication) -> None:
        self.verify_ownership(application)
        application.review(self.id)

    def shortlist_application(self, application: JobApplication) -> None:
        self.verify_ownership(application)
        application.shortlist(self.id)

    def accept_application(self, application: JobApplication) -> None:
        self.verify_ownership(application)
        application.accept(self.id)

    def reject_application(
        self, application: JobApplication, reason: str | None = None
    ) -> None:
        self.verify_ownership(application)
        application.reject(self.id, reason)

    def perform(
        self, action: str, application: JobApplication, reason: str | None = None
    ) -> None:
        """Dispatch a canonical action name to the matching method above."""
        if action in UNSUPPORTED_ACTIONS:
            self.verify_ownership(application)
            application.refuse(action)
            return
        if action == "reject":
            self.reject_application(application, reason)
            return
        {
            "review": self.review_application,
            "shortlist": self.shortlist_application,
            "accept": self.accept_application,
        }[action](application)
