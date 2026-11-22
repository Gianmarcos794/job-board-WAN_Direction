from __future__ import annotations

from sqlmodel import Session, select, update

from app.models.application import Application
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.status_change import StatusChange
from app.models.subscription import Subscription
from app.models.user import User


class BoardUserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)


class ListingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, listing_id: int) -> Listing | None:
        return self.db.get(Listing, listing_id)

    def add(self, listing: Listing) -> Listing:
        self.db.add(listing)
        self.db.flush()
        return listing

    def list_all(self) -> list[Listing]:
        return list(self.db.exec(select(Listing).order_by(Listing.id)).all())

    def list_by_status(self, status: str) -> list[Listing]:
        return list(
            self.db.exec(
                select(Listing).where(Listing.status == status).order_by(Listing.id)
            ).all()
        )


class ApplicationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, application_id: int) -> Application | None:
        return self.db.get(Application, application_id)

    def add(self, application: Application) -> Application:
        self.db.add(application)
        self.db.flush()
        return application

    def save_status_if_unchanged(
        self, application: Application, expected_status: str
    ) -> bool:
        """Write status (and rejection reason) only if the stored status is
        still ``expected_status``. Returns False when another request changed
        it first, so two concurrent transitions cannot both succeed."""
        with self.db.no_autoflush:
            result = self.db.exec(
                update(Application)
                .where(
                    Application.id == application.id,
                    Application.status == expected_status,
                )
                .values(
                    status=application.status,
                    rejection_reason=application.rejection_reason,
                )
                .execution_options(synchronize_session=False)
            )
        return result.rowcount == 1

    def list_for_graduate(self, graduate_id: int) -> list[Application]:
        return list(
            self.db.exec(
                select(Application)
                .where(Application.graduate_id == graduate_id)
                .order_by(Application.id)
            ).all()
        )

    def list_for_listing(self, listing_id: int) -> list[Application]:
        return list(
            self.db.exec(
                select(Application)
                .where(Application.listing_id == listing_id)
                .order_by(Application.id)
            ).all()
        )


class StatusChangeRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_all(self, changes: list[StatusChange]) -> None:
        for change in changes:
            self.db.add(change)
        self.db.flush()

    def list_for_application(self, application_id: int) -> list[StatusChange]:
        return list(
            self.db.exec(
                select(StatusChange)
                .where(StatusChange.application_id == application_id)
                .order_by(StatusChange.id)
            ).all()
        )


class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, notification: Notification) -> Notification:
        self.db.add(notification)
        return notification

    def list_for_user(self, user_id: int) -> list[Notification]:
        return list(
            self.db.exec(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.id)
            ).all()
        )


class NotificationPreferenceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: int, kind: str) -> NotificationPreference | None:
        return self.db.exec(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.kind == kind,
            )
        ).first()

    def list_for_user(self, user_id: int) -> list[NotificationPreference]:
        return list(
            self.db.exec(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user_id
                )
            ).all()
        )

    def set_email(self, user_id: int, kind: str, email: bool) -> NotificationPreference:
        pref = self.get(user_id, kind)
        if pref is None:
            pref = NotificationPreference(user_id=user_id, kind=kind)
        pref.email = email
        self.db.add(pref)
        return pref


class SubscriptionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_for_graduate(self, graduate_id: int) -> Subscription | None:
        return self.db.exec(
            select(Subscription).where(Subscription.graduate_id == graduate_id)
        ).one_or_none()

    def add(self, subscription: Subscription) -> Subscription:
        self.db.add(subscription)
        return subscription

    def list_all(self) -> list[Subscription]:
        return list(self.db.exec(select(Subscription)).all())
