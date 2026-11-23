"""Application create / read / status transitions (State pattern)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.database import engine
from app.models.application import Application
from app.models.listing import Listing
from app.models.user import User
from app.repositories.board import (
    ApplicationRepository,
    NotificationRepository,
    StatusChangeRepository,
)
from app.services.application_states import IllegalTransitionError
from app.services.board_auth import get_user
from app.services.employer import (
    ACTION_ALIASES,
    LIFECYCLE_ACTIONS,
    Employer,
    canonical_action,
)
from app.services.errors import ForbiddenError
from app.services.job_application import JobApplication
from app.services.listings import check_eligible_graduate, is_open
from app.services.notifications import ApplicantNotifier


def _fail(status_code: int, error: str) -> dict[str, Any]:
    return {"ok": False, "status_code": status_code, "error": error}


def apply(
    graduate_id: int,
    listing_id: int,
    cover_letter: str,
    resume_text: str | None = None,
) -> dict[str, Any]:
    user = get_user(graduate_id)
    failure = check_eligible_graduate(user)
    if failure:
        return failure

    with Session(engine) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            return _fail(404, "listing not found")
        if not is_open(listing):
            return _fail(409, "listing is not open for applications")
        if listing.apply_mode == "external":
            # FR13: external listings hold no applications on the board.
            result = _fail(
                409,
                "this listing takes applications off-platform; "
                f"apply at {listing.external_url}",
            )
            result["external_url"] = listing.external_url
            return result

        existing = session.exec(
            select(Application).where(
                Application.graduate_id == graduate_id,
                Application.listing_id == listing_id,
            )
        ).first()
        if existing is not None:
            return _fail(409, "application already submitted for this listing")

        resume = resume_text or user.get("resume_text") or ""
        application = Application(
            listing_id=listing_id,
            graduate_id=graduate_id,
            cover_letter=cover_letter,
            resume_text=resume,
            status="submitted",
        )
        session.add(application)
        session.commit()
        session.refresh(application)
        return {
            "ok": True,
            "application_id": application.id,
            "status": "submitted",
        }


def my_applications(graduate_id: int) -> list[dict[str, Any]]:
    with Session(engine) as session:
        rows = session.exec(
            select(Application)
            .where(Application.graduate_id == graduate_id)
            .order_by(Application.id)
        ).all()
        # row.status is the single source of truth for the lifecycle.
        return [row.model_dump() for row in rows]


def listing_applicants(viewer_id: int, listing_id: int) -> dict[str, Any]:
    """FR19: the owning employer (or an admin) sees a listing's applicants."""
    viewer = get_user(viewer_id)
    if not viewer:
        return _fail(404, "unknown user")
    with Session(engine) as session:
        listing = session.get(Listing, listing_id)
        if listing is None:
            return _fail(404, "listing not found")
        is_owner = viewer["role"] == "employer" and listing.employer_id == viewer["id"]
        if not (is_owner or viewer["role"] == "admin"):
            return _fail(403, "only the employer who owns this listing can see its applicants")
        rows = session.exec(
            select(Application)
            .where(Application.listing_id == listing_id)
            .order_by(Application.id)
        ).all()
        return {"ok": True, "applicants": [r.model_dump() for r in rows]}


def export_applicants(admin_id: int, listing_id: int) -> dict[str, Any]:
    admin = get_user(admin_id)
    if not admin or admin["role"] != "admin":
        return _fail(403, "admin only")
    result = listing_applicants(admin_id, listing_id)
    if not result.get("ok"):
        return result
    return {"ok": True, "format": "json", "package": result["applicants"]}


def transition_application(
    actor_id: int, application_id: int, action: str, reason: str | None = None
) -> dict[str, Any]:
    """Move an application through its lifecycle on behalf of its employer.

    Legality is decided by the application's State object; ownership by
    Employer. The new status and its StatusChange history rows are saved in a
    single commit. Failures carry an HTTP ``status_code``:
    400 bad action, 403 not allowed, 404 missing, 409 illegal transition.
    """
    with Session(engine) as session:
        actor = session.get(User, actor_id)
        if actor is None or actor.role != "employer":
            return _fail(403, "only employers can change application status")

        action = canonical_action(action)
        if action is None:
            valid = sorted({*LIFECYCLE_ACTIONS, *ACTION_ALIASES})
            return _fail(400, f"action must be one of {valid}")

        row = session.get(Application, application_id)
        if row is None:
            return _fail(404, "application not found")
        listing = session.get(Listing, row.listing_id)
        if listing is None:
            return _fail(404, "listing not found")

        candidate = session.get(User, row.graduate_id)
        history = StatusChangeRepository(session).list_for_application(row.id)
        try:
            job_app = JobApplication(row, listing, candidate, history)
        except ValueError as e:  # stored status is not a known state
            return _fail(409, str(e))

        old_status = row.status
        try:
            Employer(actor).perform(action, job_app, reason)
        except ForbiddenError as e:
            return _fail(403, str(e))
        except IllegalTransitionError as e:
            result = _fail(409, str(e))
            result["allowed"] = e.allowed
            return result

        if not ApplicationRepository(session).save_status_if_unchanged(row, old_status):
            session.rollback()
            return _fail(
                409, "application was changed by another request; reload and retry"
            )
        session.add(row)
        StatusChangeRepository(session).add_all(job_app.unsaved_changes())

        applicant = job_app.candidate
        notifier = ApplicantNotifier(NotificationRepository(session))
        email = notifier.stage_status_change(applicant, row, listing)

        # Status, history and notification are saved together (NFR7).
        session.commit()
        job_app.mark_saved()
        session.refresh(row)

    # Email goes out only after the commit succeeded.
    notifier.deliver([email])
    return {"ok": True, "application": row.model_dump()}


def application_history(actor_id: int, application_id: int) -> dict[str, Any]:
    """Status-change history, visible to admins and the listing's employer."""
    with Session(engine) as session:
        actor = session.get(User, actor_id)
        row = session.get(Application, application_id)
        if row is None:
            return _fail(404, "application not found")
        listing = session.get(Listing, row.listing_id)
        allowed = actor is not None and (
            actor.role == "admin"
            or (listing is not None and listing.employer_id == actor.id)
        )
        if not allowed:
            return _fail(403, "only admins or the listing's employer can view history")
        changes = StatusChangeRepository(session).list_for_application(application_id)
        return {"ok": True, "history": [c.model_dump(mode="json") for c in changes]}
