"""Job-board scenario JSON API (OpenAPI /docs)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import applications, listings, notifications as notification_service
from app.services import board_auth as auth
from app.services.errors import ForbiddenError, NotFoundError

router = APIRouter(tags=["job-board"])


class LoginBody(BaseModel):
    email: str
    password: str


class ListingBody(BaseModel):
    title: str
    company: str
    description: str
    apply_mode: str = Field(description="collect | external")
    deadline: str = Field(description="ISO date YYYY-MM-DD")
    external_url: str | None = None
    actor_id: int = Field(description="Acting user id (demo auth)")


class ActorBody(BaseModel):
    actor_id: int


class ApplyBody(BaseModel):
    listing_id: int
    cover_letter: str
    resume_text: str | None = None
    actor_id: int


class RejectListingBody(BaseModel):
    actor_id: int
    reason: str | None = Field(default=None, description="What the employer should change")


class DeadlineBody(BaseModel):
    deadline: str = Field(description="New ISO date YYYY-MM-DD, after the current one")
    actor_id: int


class PreferenceBody(BaseModel):
    kind: str = Field(description="application_status | new_listing")
    email: bool = Field(description="false = in-app only for this kind")
    actor_id: int


def checked(result: dict[str, Any]) -> dict[str, Any]:
    """Turn a service failure into the HTTP error code it carries."""
    if not result.get("ok"):
        detail: dict[str, Any] | str = result.get("error", "request failed")
        extra = {
            key: value
            for key, value in result.items()
            if key not in {"ok", "status_code", "error"}
        }
        if extra:
            detail = {"error": detail, **extra}
        raise HTTPException(status_code=result.get("status_code", 400), detail=detail)
    return result


class TransitionBody(BaseModel):
    action: str = Field(
        description="review | shortlist | accept | reject "
        "(aliases: mark_reviewed=review, hire=accept)"
    )
    actor_id: int
    reason: str | None = Field(default=None, description="Optional, for reject")


@router.post("/auth/login")
def login(body: LoginBody) -> dict[str, Any]:
    user = auth.login(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"user": user}


@router.post("/graduates/subscribe")
def subscribe(body: ActorBody) -> dict[str, Any]:
    return checked(listings.subscribe(body.actor_id))


@router.get("/jobs")
def jobs() -> dict[str, Any]:
    return {"jobs": listings.list_jobs_for_graduates()}


@router.get("/admin/listings/pending")
def pending(actor_id: int) -> dict[str, Any]:
    actor = auth.get_user(actor_id)
    if not actor or actor["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return {"listings": listings.list_pending_for_admin()}


@router.post("/employer/listings")
def submit_listing(body: ListingBody) -> dict[str, Any]:
    return checked(listings.submit_listing(
        body.actor_id,
        body.title,
        body.company,
        body.description,
        body.apply_mode,
        body.deadline,
        body.external_url,
    ))


@router.post("/admin/listings/{listing_id}/publish")
def publish(listing_id: int, body: ActorBody) -> dict[str, Any]:
    return checked(listings.publish_listing(body.actor_id, listing_id))


@router.post("/admin/listings/{listing_id}/notify")
def notify(listing_id: int, body: ActorBody) -> dict[str, Any]:
    return checked(listings.notify_subscribers(body.actor_id, listing_id))


@router.post("/graduates/applications")
def apply(body: ApplyBody) -> dict[str, Any]:
    return checked(applications.apply(
        body.actor_id, body.listing_id, body.cover_letter, body.resume_text
    ))


@router.get("/graduates/applications")
def my_apps(actor_id: int) -> dict[str, Any]:
    return {"applications": applications.my_applications(actor_id)}


@router.get("/listings/{listing_id}/applicants")
def applicants(listing_id: int, actor_id: int) -> dict[str, Any]:
    return checked(applications.listing_applicants(actor_id, listing_id))


@router.get("/admin/listings/{listing_id}/export")
def export(listing_id: int, actor_id: int) -> dict[str, Any]:
    return checked(applications.export_applicants(actor_id, listing_id))


@router.post("/applications/{application_id}/transition")
def transition(application_id: int, body: TransitionBody) -> dict[str, Any]:
    return checked(applications.transition_application(
        body.actor_id, application_id, body.action, body.reason
    ))


@router.get("/applications/{application_id}/history")
def history(application_id: int, actor_id: int) -> dict[str, Any]:
    return checked(applications.application_history(actor_id, application_id))


@router.get("/notifications")
def notifications(actor_id: int) -> dict[str, Any]:
    try:
        return {"notifications": notification_service.list_notifications(actor_id)}
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/graduates/notification-preferences")
def get_preferences(actor_id: int) -> dict[str, Any]:
    """FR22: which notification kinds also go by email (in-app is always on)."""
    try:
        return {"email": notification_service.get_email_preferences(actor_id)}
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.put("/graduates/notification-preferences")
def set_preference(body: PreferenceBody) -> dict[str, Any]:
    try:
        return {
            "email": notification_service.set_email_preference(
                body.actor_id, body.kind, body.email
            )
        }
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/listings/{listing_id}/reject")
def reject_listing(listing_id: int, body: RejectListingBody) -> dict[str, Any]:
    return checked(listings.reject_listing(body.actor_id, listing_id, body.reason))


@router.post("/admin/listings/{listing_id}/unpublish")
def unpublish_listing(listing_id: int, body: ActorBody) -> dict[str, Any]:
    return checked(listings.unpublish_listing(body.actor_id, listing_id))


@router.post("/admin/listings/{listing_id}/deadline")
def extend_deadline(listing_id: int, body: DeadlineBody) -> dict[str, Any]:
    return checked(listings.extend_deadline(body.actor_id, listing_id, body.deadline))


@router.post("/employer/listings/{listing_id}/filled")
def mark_filled(listing_id: int, body: ActorBody) -> dict[str, Any]:
    return checked(listings.mark_filled(body.actor_id, listing_id))


@router.post("/employer/listings/{listing_id}/resubmit")
def resubmit_listing(listing_id: int, body: ListingBody) -> dict[str, Any]:
    return checked(
        listings.resubmit_listing(
            body.actor_id,
            listing_id,
            body.title,
            body.company,
            body.description,
            body.apply_mode,
            body.deadline,
            body.external_url,
        )
    )
