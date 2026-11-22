"""Listing submit / review / publish / browse helpers.

Listing statuses:

    pending --publish--> published --unpublish--> unpublished --publish--> published
       |                     |
       +--reject--> rejected  +--mark filled (owner)--> filled
                       |
                       +--resubmit (owner)--> pending

A published listing is "open" until its deadline; after that it is hidden
from graduates and refuses applications (FR14) without changing status.

Every failure carries an HTTP ``status_code`` (400 bad input, 403 not
allowed, 404 missing, 409 wrong listing status).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session

from app.database import engine
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.user import User
from app.repositories.board import (
    ListingRepository,
    NotificationPreferenceRepository,
    SubscriptionRepository,
)
from app.models.subscription import Subscription
from app.services.board_auth import get_user
from app.services.email import EmailMessage
from app.services.notifications import (
    LISTING_REJECTED,
    NEW_LISTING,
    deliver_emails,
    wants_email,
)

ELIGIBLE_PROGRAMMES = {"DCIT"}

PENDING = "pending"
PUBLISHED = "published"
REJECTED = "rejected"
UNPUBLISHED = "unpublished"
FILLED = "filled"


def _fail(status_code: int, error: str) -> dict[str, Any]:
    return {"ok": False, "status_code": status_code, "error": error}


def is_eligible(user: dict[str, Any] | None) -> bool:
    """FR9: only graduates from an eligible programme (currently DCIT)."""
    return (
        user is not None
        and user.get("role") == "graduate"
        and user.get("programme") in ELIGIBLE_PROGRAMMES
    )


def check_eligible_graduate(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a failure dict if the user may not subscribe/apply, else None."""
    if user is None:
        return _fail(404, "unknown user")
    if user["role"] != "graduate":
        return _fail(403, "graduates only")
    if not is_eligible(user):
        return _fail(
            403,
            "only graduates from an eligible programme "
            f"({', '.join(sorted(ELIGIBLE_PROGRAMMES))}) can subscribe or apply",
        )
    return None


def _parse_deadline(deadline: str) -> date | None:
    try:
        return date.fromisoformat(deadline)
    except (TypeError, ValueError):
        return None


def is_open(listing: Listing) -> bool:
    """Published and not past its deadline (deadlines are ISO YYYY-MM-DD)."""
    return listing.status == PUBLISHED and listing.deadline >= date.today().isoformat()


def subscribe(graduate_id: int) -> dict[str, Any]:
    failure = check_eligible_graduate(get_user(graduate_id))
    if failure:
        return failure
    with Session(engine) as session:
        subscriptions = SubscriptionRepository(session)
        if subscriptions.get_for_graduate(graduate_id) is None:
            subscriptions.add(Subscription(graduate_id=graduate_id))
            session.commit()
    return {"ok": True}


def list_jobs_for_graduates() -> list[dict[str, Any]]:
    """Only published listings whose deadline has not passed."""
    with Session(engine) as session:
        rows = ListingRepository(session).list_by_status(PUBLISHED)
        return [r.model_dump() for r in rows if is_open(r)]


def list_pending_for_admin() -> list[dict[str, Any]]:
    with Session(engine) as session:
        rows = ListingRepository(session).list_by_status(PENDING)
        return [r.model_dump() for r in rows]


def _validate_listing_fields(
    apply_mode: str, deadline: str, external_url: str | None
) -> dict[str, Any] | None:
    if apply_mode not in {"collect", "external"}:
        return _fail(400, "apply_mode must be collect|external")
    if apply_mode == "external" and not external_url:
        return _fail(400, "external_url required")
    if _parse_deadline(deadline) is None:
        return _fail(400, "deadline must be an ISO date (YYYY-MM-DD)")
    return None


def submit_listing(
    employer_id: int,
    title: str,
    company: str,
    description: str,
    apply_mode: str,
    deadline: str,
    external_url: str | None = None,
) -> dict[str, Any]:
    """FR11: a new listing starts pending until an admin publishes it."""
    user = get_user(employer_id)
    if not user or user["role"] != "employer":
        return _fail(403, "employers only")
    failure = _validate_listing_fields(apply_mode, deadline, external_url)
    if failure:
        return failure

    with Session(engine) as session:
        listing = ListingRepository(session).add(
            Listing(
                employer_id=employer_id,
                title=title,
                company=company,
                description=description,
                apply_mode=apply_mode,
                external_url=external_url if apply_mode == "external" else None,
                deadline=deadline,
                status=PENDING,
            )
        )
        session.commit()
        session.refresh(listing)
        return {"ok": True, "listing_id": listing.id, "status": listing.status}


def resubmit_listing(
    employer_id: int,
    listing_id: int,
    title: str,
    company: str,
    description: str,
    apply_mode: str,
    deadline: str,
    external_url: str | None = None,
) -> dict[str, Any]:
    """FR12: the owner revises a rejected listing and sends it back to pending."""
    failure = _validate_listing_fields(apply_mode, deadline, external_url)
    if failure:
        return failure
    with Session(engine) as session:
        listing, failure = _owned_listing(session, employer_id, listing_id)
        if failure:
            return failure
        if listing.status != REJECTED:
            return _fail(409, f"only a rejected listing can be resubmitted (it is {listing.status})")
        listing.title = title
        listing.company = company
        listing.description = description
        listing.apply_mode = apply_mode
        listing.external_url = external_url if apply_mode == "external" else None
        listing.deadline = deadline
        listing.status = PENDING
        listing.rejection_reason = None
        session.add(listing)
        session.commit()
        session.refresh(listing)
        return {"ok": True, "listing": listing.model_dump()}


def _admin_listing(
    session: Session, admin_id: int, listing_id: int
) -> tuple[Listing | None, dict[str, Any] | None]:
    admin = session.get(User, admin_id)
    if admin is None or admin.role != "admin":
        return None, _fail(403, "admin only")
    listing = ListingRepository(session).get(listing_id)
    if listing is None:
        return None, _fail(404, "listing not found")
    return listing, None


def _owned_listing(
    session: Session, employer_id: int, listing_id: int
) -> tuple[Listing | None, dict[str, Any] | None]:
    employer = session.get(User, employer_id)
    if employer is None or employer.role != "employer":
        return None, _fail(403, "employers only")
    listing = ListingRepository(session).get(listing_id)
    if listing is None:
        return None, _fail(404, "listing not found")
    if listing.employer_id != employer.id:
        return None, _fail(403, "only the employer who owns this listing can do this")
    return listing, None


def _stage_new_listing_notifications(
    session: Session, listing: Listing
) -> list[EmailMessage | None]:
    """In-app notification for every subscriber; emails returned for after commit."""
    prefs = NotificationPreferenceRepository(session)
    message = f"New job published: {listing.title} at {listing.company}"
    emails: list[EmailMessage | None] = []
    for sub in SubscriptionRepository(session).list_all():
        session.add(Notification(user_id=sub.graduate_id, message=message, kind=NEW_LISTING))
        graduate = session.get(User, sub.graduate_id)
        if graduate and wants_email(prefs, graduate.id, NEW_LISTING):
            emails.append(
                EmailMessage(to=graduate.email, subject=f"New job: {listing.title}", body=message)
            )
    return emails


def publish_listing(admin_id: int, listing_id: int) -> dict[str, Any]:
    """FR18: publishing notifies every subscriber in the same commit."""
    with Session(engine) as session:
        listing, failure = _admin_listing(session, admin_id, listing_id)
        if failure:
            return failure
        if listing.status not in {PENDING, UNPUBLISHED}:
            return _fail(409, f"cannot publish a {listing.status} listing")
        listing.status = PUBLISHED
        session.add(listing)
        emails = _stage_new_listing_notifications(session, listing)
        session.commit()
    deliver_emails(emails)
    return {"ok": True, "status": PUBLISHED, "notified": len(emails)}


def notify_subscribers(admin_id: int, listing_id: int) -> dict[str, Any]:
    """Manual re-send, kept for the admin; publishing already notifies."""
    with Session(engine) as session:
        listing, failure = _admin_listing(session, admin_id, listing_id)
        if failure:
            return failure
        if listing.status != PUBLISHED:
            return _fail(409, f"cannot notify about a {listing.status} listing")
        emails = _stage_new_listing_notifications(session, listing)
        count = len(SubscriptionRepository(session).list_all())
        session.commit()
    deliver_emails(emails)
    return {"ok": True, "count": count}


def _admin_set_status(
    admin_id: int, listing_id: int, allowed_from: set[str], new_status: str, verb: str
) -> dict[str, Any]:
    with Session(engine) as session:
        listing, failure = _admin_listing(session, admin_id, listing_id)
        if failure:
            return failure
        if listing.status not in allowed_from:
            return _fail(409, f"cannot {verb} a {listing.status} listing")
        listing.status = new_status
        session.add(listing)
        session.commit()
        session.refresh(listing)
        return {"ok": True, "listing": listing.model_dump()}


def reject_listing(
    admin_id: int, listing_id: int, reason: str | None = None
) -> dict[str, Any]:
    """FR12: admin rejects a pending listing; the employer is told why and can
    revise and resubmit (ADMIN-Q5)."""
    with Session(engine) as session:
        listing, failure = _admin_listing(session, admin_id, listing_id)
        if failure:
            return failure
        if listing.status != PENDING:
            return _fail(409, f"cannot reject a {listing.status} listing")
        listing.status = REJECTED
        listing.rejection_reason = (reason or "").strip() or None
        session.add(listing)
        message = f"Your listing {listing.title} was rejected and needs changes before it can be published."
        if listing.rejection_reason:
            message += f" Reason: {listing.rejection_reason}"
        session.add(
            Notification(user_id=listing.employer_id, message=message, kind=LISTING_REJECTED)
        )
        session.commit()
        session.refresh(listing)
        return {"ok": True, "listing": listing.model_dump()}


def unpublish_listing(admin_id: int, listing_id: int) -> dict[str, Any]:
    """FR12: admin takes a live listing down. Applications keep their status (FR17)."""
    return _admin_set_status(admin_id, listing_id, {PUBLISHED}, UNPUBLISHED, "unpublish")


def extend_deadline(admin_id: int, listing_id: int, new_deadline: str) -> dict[str, Any]:
    """FR15: a later deadline makes a closed listing visible again."""
    new_date = _parse_deadline(new_deadline)
    if new_date is None:
        return _fail(400, "deadline must be an ISO date (YYYY-MM-DD)")
    with Session(engine) as session:
        listing, failure = _admin_listing(session, admin_id, listing_id)
        if failure:
            return failure
        if listing.status in {REJECTED, FILLED}:
            return _fail(409, f"cannot extend a {listing.status} listing")
        if new_date.isoformat() <= listing.deadline:
            return _fail(400, f"new deadline must be after the current one ({listing.deadline})")
        listing.deadline = new_date.isoformat()
        session.add(listing)
        session.commit()
        session.refresh(listing)
        return {"ok": True, "listing": listing.model_dump()}


def mark_filled(employer_id: int, listing_id: int) -> dict[str, Any]:
    """FR16: the owner closes a published listing. Applications keep their status (FR17)."""
    with Session(engine) as session:
        listing, failure = _owned_listing(session, employer_id, listing_id)
        if failure:
            return failure
        if listing.status != PUBLISHED:
            return _fail(409, f"cannot mark a {listing.status} listing as filled")
        listing.status = FILLED
        session.add(listing)
        session.commit()
        session.refresh(listing)
        return {"ok": True, "listing": listing.model_dump()}
