from __future__ import annotations

import logging

from app.models.application import Application
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.user import User
from sqlmodel import Session

from app.repositories.board import BoardUserRepository, NotificationRepository
from app.services.guards import require_user
from app.services.email import EmailMessage, EmailSender, get_email_sender

logger = logging.getLogger(__name__)


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
        self, notifications: NotificationRepository, sender: EmailSender | None = None
    ) -> None:
        self.notifications = notifications
        self._sender = sender

    @property
    def sender(self) -> EmailSender:
        return self._sender or get_email_sender()

    def stage_status_change(
        self, applicant: User, application: Application, listing: Listing
    ) -> EmailMessage:
        message = status_message(listing, application)
        self.notifications.add(
            Notification(
                user_id=applicant.id,
                message=message,
                kind="application_status",
                application_id=application.id,
            )
        )
        return EmailMessage(
            to=applicant.email,
            subject=f"Application update: {listing.title}",
            body=message,
        )

    def deliver(self, emails: list[EmailMessage]) -> None:
        for email in emails:
            try:
                self.sender.send(email)
            except Exception:  
                logger.exception("Could not send email to %s", email.to)


class NotificationService:
    """Read side: a user sees only their own in-app notifications."""

    def __init__(self, db: Session) -> None:
        self.users = BoardUserRepository(db)
        self.notifications = NotificationRepository(db)

    def for_user(self, user_id: int) -> list[dict]:
        require_user(self.users, user_id)
        return [
            n.model_dump(mode="json")
            for n in reversed(self.notifications.list_for_user(user_id))
        ]
