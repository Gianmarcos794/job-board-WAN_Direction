from __future__ import annotations

import logging

from app.database import engine
from app.models.application import Application
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.user import User
from sqlmodel import Session

from app.repositories.board import (
    BoardUserRepository,
    NotificationPreferenceRepository,
    NotificationRepository,
)
from app.services.guards import require_user
from app.services.email import EmailMessage, EmailSender, get_email_sender
from app.services.errors import ForbiddenError

logger = logging.getLogger(__name__)

# Notification kinds a graduate can switch email on/off for (FR22).
APPLICATION_STATUS = "application_status"
NEW_LISTING = "new_listing"
LISTING_REJECTED = "listing_rejected"  # to the employer; in-app only
NOTIFICATION_KINDS = (APPLICATION_STATUS, NEW_LISTING)


def wants_email(prefs: NotificationPreferenceRepository, user_id: int, kind: str) -> bool:
    """Email is on unless the user switched it off for this kind."""
    pref = prefs.get(user_id, kind)
    return True if pref is None else pref.email


def deliver_emails(
    emails: list[EmailMessage | None], sender: EmailSender | None = None
) -> None:
    """Send after the DB commit; one failed email never blocks the others."""
    sender = sender or get_email_sender()
    for email in emails:
        if email is None:
            continue
        try:
            sender.send(email)
        except Exception:
            logger.exception("Could not send email to %s", email.to)


def status_message(listing: Listing, application: Application) -> str:
    text = (
        f"Your application for {listing.title} at {listing.company} "
        f"is now {application.status}."
    )
    if application.status == "rejected" and application.rejection_reason:
        text += f" Reason: {application.rejection_reason}"
    return text


class ApplicantNotifier:
    def __init__(
        self,
        notifications: NotificationRepository,
        sender: EmailSender | None = None,
        preferences: NotificationPreferenceRepository | None = None,
    ) -> None:
        self.notifications = notifications
        self._sender = sender
        self.preferences = preferences or NotificationPreferenceRepository(
            notifications.db
        )

    @property
    def sender(self) -> EmailSender:
        return self._sender or get_email_sender()

    def stage_status_change(
        self, applicant: User, application: Application, listing: Listing
    ) -> EmailMessage | None:
        """Add the in-app notification; return the email to send after commit,
        or None if the applicant turned status emails off."""
        message = status_message(listing, application)
        self.notifications.add(
            Notification(
                user_id=applicant.id,
                message=message,
                kind=APPLICATION_STATUS,
                application_id=application.id,
            )
        )
        if not wants_email(self.preferences, applicant.id, APPLICATION_STATUS):
            return None
        return EmailMessage(
            to=applicant.email,
            subject=f"Application update: {listing.title}",
            body=message,
        )

    def deliver(self, emails: list[EmailMessage | None]) -> None:
        deliver_emails(emails, self.sender)


class NotificationService:
    """Read side: a user sees only their own in-app notifications."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = BoardUserRepository(db)
        self.notifications = NotificationRepository(db)
        self.preferences = NotificationPreferenceRepository(db)

    def for_user(self, user_id: int) -> list[dict]:
        require_user(self.users, user_id)
        return [
            n.model_dump(mode="json")
            for n in reversed(self.notifications.list_for_user(user_id))
        ]

    def email_preferences(self, user_id: int) -> dict[str, bool]:
        self._require_graduate(user_id)
        return {
            kind: wants_email(self.preferences, user_id, kind)
            for kind in NOTIFICATION_KINDS
        }

    def set_email_preference(self, user_id: int, kind: str, email: bool) -> dict[str, bool]:
        self._require_graduate(user_id)
        if kind not in NOTIFICATION_KINDS:
            raise ValueError(f"kind must be one of {list(NOTIFICATION_KINDS)}")
        self.preferences.set_email(user_id, kind, email)
        self.db.commit()
        return self.email_preferences(user_id)

    def _require_graduate(self, user_id: int) -> User:
        user = require_user(self.users, user_id)
        if user.role != "graduate":
            raise ForbiddenError("only graduates have notification preferences")
        return user


# Session-owning entry points for the router (keeps DB code out of routes, NFR9).


def list_notifications(user_id: int) -> list[dict]:
    with Session(engine) as db:
        return NotificationService(db).for_user(user_id)


def get_email_preferences(user_id: int) -> dict[str, bool]:
    with Session(engine) as db:
        return NotificationService(db).email_preferences(user_id)


def set_email_preference(user_id: int, kind: str, email: bool) -> dict[str, bool]:
    with Session(engine) as db:
        return NotificationService(db).set_email_preference(user_id, kind, email)
