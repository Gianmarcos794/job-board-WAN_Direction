"""Scenario smoke and lifecycle integration tests."""

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.database import engine
from app.models.application import Application
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.user import User
from app.main import app
from app.seed import USERS, seed
from app.utilities.security import verify_password


def setup_module() -> None:
    seed(reset=True)


client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["scenario"] == "job-board"


def test_api_login_seed_admin():
    r = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "admin-dev"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin"


def test_jobs_endpoint_returns_list():
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json()["jobs"], list)
    assert len(r.json()["jobs"]) >= 1


def _user_id(email: str) -> int:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        assert user.id is not None
        return user.id


def _listing_id(title: str) -> int:
    with Session(engine) as session:
        listing = session.exec(select(Listing).where(Listing.title == title)).one()
        assert listing.id is not None
        return listing.id


def _apply(graduate_id: int, listing_id: int, cover_letter: str):
    with Session(engine) as session:
        session.exec(
            delete(Application).where(
                Application.graduate_id == graduate_id,
                Application.listing_id == listing_id,
            )
        )
        session.commit()

    return client.post(
        "/api/graduates/applications",
        json={
            "listing_id": listing_id,
            "cover_letter": cover_letter,
            "resume_text": f"{cover_letter} resume",
            "actor_id": graduate_id,
        },
    )


def test_application_lifecycle_accept_path_and_history():
    graduate_id = _user_id("grad.dcit@example.com")
    employer_id = _user_id("employer@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    applied = _apply(graduate_id, listing_id, "Lifecycle API test")
    assert applied.status_code == 200
    application_id = applied.json()["application_id"]
    assert applied.json()["status"] == "submitted"

    for action, expected_status in (
        ("review", "reviewed"),
        ("shortlist", "shortlisted"),
        ("accept", "hired"),
    ):
        transitioned = client.post(
            f"/api/applications/{application_id}/transition",
            json={"action": action, "actor_id": employer_id},
        )
        assert transitioned.status_code == 200
        assert transitioned.json()["application"]["status"] == expected_status

    history = client.get(
        f"/api/applications/{application_id}/history",
        params={"actor_id": employer_id},
    )
    assert history.status_code == 200
    changes = history.json()["history"]
    assert [(change["old_status"], change["new_status"]) for change in changes] == [
        ("submitted", "reviewed"),
        ("reviewed", "shortlisted"),
        ("shortlisted", "hired"),
    ]

    graduate_apps = client.get(
        "/api/graduates/applications",
        params={"actor_id": graduate_id},
    )
    assert graduate_apps.status_code == 200
    application = next(
        app
        for app in graduate_apps.json()["applications"]
        if app["id"] == application_id
    )
    assert application["status"] == "hired"
    assert application["rejection_reason"] is None


def test_application_lifecycle_rejects_illegal_transition_and_wrong_employer():
    graduate_id = _user_id("grad.dcit@example.com")
    owner_id = _user_id("employer@example.com")
    other_employer_id = _user_id("employer2@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    applied = _apply(graduate_id, listing_id, "Authorization API test")
    assert applied.status_code == 200
    application_id = applied.json()["application_id"]

    wrong_owner = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "review", "actor_id": other_employer_id},
    )
    assert wrong_owner.status_code == 403

    illegal_skip = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "accept", "actor_id": owner_id},
    )
    assert illegal_skip.status_code == 409
    assert illegal_skip.json()["detail"]
    assert illegal_skip.json()["detail"] != ""

    reviewed = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "review", "actor_id": owner_id},
    )
    assert reviewed.status_code == 200

    illegal_reopen = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "reopen", "actor_id": owner_id},
    )
    assert illegal_reopen.status_code == 409

    with Session(engine) as session:
        stored = session.get(Application, application_id)
        assert stored is not None
        assert stored.status == "reviewed"


def test_rejection_records_reason_and_prevents_reopen():
    graduate_id = _user_id("grad.dcit@example.com")
    owner_id = _user_id("employer@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    applied = _apply(graduate_id, listing_id, "Rejection test")
    assert applied.status_code == 200
    application_id = applied.json()["application_id"]

    reviewed = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "review", "actor_id": owner_id},
    )
    assert reviewed.status_code == 200

    rejected = client.post(
        f"/api/applications/{application_id}/transition",
        json={
            "action": "reject",
            "reason": "Insufficient relevant experience",
            "actor_id": owner_id,
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["application"]["status"] == "rejected"
    assert (
        rejected.json()["application"]["rejection_reason"]
        == "Insufficient relevant experience"
    )

    reopen = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "reopen", "actor_id": owner_id},
    )
    assert reopen.status_code == 409

    with Session(engine) as session:
        stored = session.get(Application, application_id)
        assert stored is not None
        assert stored.status == "rejected"
        assert stored.rejection_reason == "Insufficient relevant experience"


def test_admin_cannot_transition_application():
    graduate_id = _user_id("grad.dcit@example.com")
    admin_id = _user_id("admin@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    applied = _apply(graduate_id, listing_id, "Admin denial test")
    assert applied.status_code == 200
    application_id = applied.json()["application_id"]

    denied = client.post(
        f"/api/applications/{application_id}/transition",
        json={"action": "review", "actor_id": admin_id},
    )
    assert denied.status_code == 403
    assert "only employers" in denied.json()["detail"].lower()


def test_duplicate_application_is_rejected():
    graduate_id = _user_id("grad.dcit@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    first = _apply(graduate_id, listing_id, "First duplicate test")

    duplicate = client.post(
        "/api/graduates/applications",
        json={
            "listing_id": listing_id,
            "cover_letter": "Second duplicate test",
            "resume_text": "Second duplicate resume",
            "actor_id": graduate_id,
        },
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert "already submitted" in duplicate.json()["detail"]


def test_expired_listing_is_hidden_and_rejects_apply():
    graduate_id = _user_id("grad.dcit@example.com")
    listing_id = _listing_id("Expired Analyst Role")

    jobs = client.get("/api/jobs").json()["jobs"]
    assert all(job["id"] != listing_id for job in jobs)

    applied = client.post(
        "/api/graduates/applications",
        json={
            "listing_id": listing_id,
            "cover_letter": "Expired listing test",
            "resume_text": "Expired listing resume",
            "actor_id": graduate_id,
        },
    )
    assert applied.status_code == 409
    assert "not open" in applied.json()["detail"]


def test_external_listing_redirects_without_creating_application():
    graduate_id = _user_id("grad.dcit@example.com")

    with Session(engine) as session:
        external = Listing(
            employer_id=_user_id("employer@example.com"),
            title="External Lifecycle Test",
            company="External Co",
            description="Apply externally",
            apply_mode="external",
            external_url="https://example.com/lifecycle-test",
            deadline="2099-12-31",
            status="published",
        )
        session.add(external)
        session.commit()
        session.refresh(external)
        external_id = external.id

    applied = client.post(
        "/api/graduates/applications",
        json={
            "listing_id": external_id,
            "cover_letter": "Should not be collected",
            "resume_text": "Should not be collected",
            "actor_id": graduate_id,
        },
    )
    assert applied.status_code == 409
    assert (
        applied.json()["detail"]["external_url"]
        == "https://example.com/lifecycle-test"
    )

    with Session(engine) as session:
        stored = session.exec(
            select(Application).where(Application.listing_id == external_id)
        ).first()
        assert stored is None


# ---------------------------------------------------------------------------
# Authentication scenario tests
# ---------------------------------------------------------------------------


def test_api_login_all_roles_and_never_leaks_password():
    for _username, email, password, role, _prog, _field, _resume in USERS:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200
        user = r.json()["user"]
        assert user["email"] == email
        assert user["role"] == role
        assert "password" not in user
        assert "password_hash" not in user


def test_api_login_rejects_invalid_credentials():
    wrong_pw = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "wrong-password"},
    )
    assert wrong_pw.status_code == 401

    unknown_email = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "admin-dev"},
    )
    assert unknown_email.status_code == 401


def test_stored_passwords_are_hashed():
    with Session(engine) as session:
        for _username, email, plaintext, _role, _prog, _field, _resume in USERS:
            user = session.exec(select(User).where(User.email == email)).one()
            assert user.password != plaintext
            assert verify_password(plaintext, user.password)


def test_ssr_login_and_admin_route_protection():
    local_client = TestClient(app)

    # Unauthenticated access to /admin is rejected
    unauth = local_client.get("/admin", follow_redirects=False)
    assert unauth.status_code == 401

    # Invalid SSR login redirects back to /login without setting access_token
    bad_login = local_client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    assert bad_login.status_code == 303
    assert bad_login.headers["location"].endswith("/login")
    assert "access_token" not in bad_login.cookies

    # Non-admin login sets cookie and redirects to /app, but /admin stays forbidden (401)
    grad_login = local_client.post(
        "/login",
        data={"username": "grad.dcit", "password": "grad-dev"},
        follow_redirects=False,
    )
    assert grad_login.status_code == 303
    assert grad_login.headers["location"].endswith("/app")
    assert "access_token" in grad_login.cookies

    grad_admin = local_client.get("/admin", follow_redirects=False)
    assert grad_admin.status_code == 401

    # Admin login sets cookie, redirects to /admin, and can access /admin
    admin_login = local_client.post(
        "/login",
        data={"username": "admin", "password": "admin-dev"},
        follow_redirects=False,
    )
    assert admin_login.status_code == 303
    assert admin_login.headers["location"].endswith("/admin")
    assert "access_token" in admin_login.cookies

    admin_page = local_client.get("/admin", follow_redirects=False)
    assert admin_page.status_code == 200


# ---------------------------------------------------------------------------
# Listings scenario tests
# ---------------------------------------------------------------------------


def _submit_listing(employer_id: int, **overrides):
    payload = {
        "title": "Test Listing",
        "company": "Test Co",
        "description": "Role description",
        "apply_mode": "collect",
        "deadline": "2099-12-31",
        "external_url": None,
        "actor_id": employer_id,
    }
    payload.update(overrides)
    return client.post("/api/employer/listings", json=payload)


def test_listing_submit_hidden_until_published_and_validation():
    employer_id = _user_id("employer@example.com")
    admin_id = _user_id("admin@example.com")
    grad_id = _user_id("grad.dcit@example.com")

    # Non-employers cannot submit listings
    assert _submit_listing(grad_id, title="Grad Attempt").status_code == 403
    assert _submit_listing(admin_id, title="Admin Attempt").status_code == 403

    # Field validation
    assert _submit_listing(employer_id, apply_mode="invalid").status_code == 400
    assert (
        _submit_listing(employer_id, apply_mode="external", external_url=None).status_code
        == 400
    )
    assert _submit_listing(employer_id, deadline="not-a-date").status_code == 400

    # Valid submission starts pending and is hidden from graduates
    created = _submit_listing(employer_id, title="Pending Dev Role")
    assert created.status_code == 200
    body = created.json()
    listing_id = body["listing_id"]
    assert body["status"] == "pending"

    jobs_before = client.get("/api/jobs").json()["jobs"]
    assert all(job["id"] != listing_id for job in jobs_before)

    pending = client.get("/api/admin/listings/pending", params={"actor_id": admin_id})
    assert pending.status_code == 200
    assert any(item["id"] == listing_id for item in pending.json()["listings"])

    # Non-admin cannot publish
    non_admin_pub = client.post(
        f"/api/admin/listings/{listing_id}/publish", json={"actor_id": employer_id}
    )
    assert non_admin_pub.status_code == 403

    # Admin publishes -> visible to graduates; publishing again returns 409
    published = client.post(
        f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id}
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    jobs_after = client.get("/api/jobs").json()["jobs"]
    assert any(job["id"] == listing_id for job in jobs_after)

    publish_again = client.post(
        f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id}
    )
    assert publish_again.status_code == 409


def test_listing_reject_notifies_employer_and_resubmit_flow():
    employer_id = _user_id("employer@example.com")
    other_employer_id = _user_id("employer2@example.com")
    admin_id = _user_id("admin@example.com")

    created = _submit_listing(employer_id, title="Needs Revision Role")
    assert created.status_code == 200
    listing_id = created.json()["listing_id"]

    # Non-admin cannot reject
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/reject",
            json={"actor_id": employer_id, "reason": "No"},
        ).status_code
        == 403
    )

    # Admin rejects with reason -> employer gets a listing_rejected notification
    rejected = client.post(
        f"/api/admin/listings/{listing_id}/reject",
        json={"actor_id": admin_id, "reason": "Add salary range"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["listing"]["status"] == "rejected"
    assert rejected.json()["listing"]["rejection_reason"] == "Add salary range"

    # Rejecting again or publishing a rejected listing returns 409
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/reject",
            json={"actor_id": admin_id, "reason": "Again"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/publish",
            json={"actor_id": admin_id},
        ).status_code
        == 409
    )

    emp_notifs = client.get(
        "/api/notifications", params={"actor_id": employer_id}
    ).json()["notifications"]
    assert any(
        n["kind"] == "listing_rejected"
        and "Needs Revision Role" in n["message"]
        and "Add salary range" in n["message"]
        for n in emp_notifs
    )

    # Non-owner cannot resubmit
    resubmit_payload = {
        "title": "Revised Role",
        "company": "Test Co",
        "description": "Updated with salary",
        "apply_mode": "collect",
        "deadline": "2099-12-31",
        "external_url": None,
        "actor_id": other_employer_id,
    }
    assert (
        client.post(
            f"/api/employer/listings/{listing_id}/resubmit", json=resubmit_payload
        ).status_code
        == 403
    )

    # Owner resubmits -> returns to pending with cleared rejection_reason
    resubmit_payload["actor_id"] = employer_id
    resubmitted = client.post(
        f"/api/employer/listings/{listing_id}/resubmit", json=resubmit_payload
    )
    assert resubmitted.status_code == 200
    listing = resubmitted.json()["listing"]
    assert listing["status"] == "pending"
    assert listing["title"] == "Revised Role"
    assert listing["rejection_reason"] is None

    # Resubmitting a non-rejected listing returns 409
    assert (
        client.post(
            f"/api/employer/listings/{listing_id}/resubmit", json=resubmit_payload
        ).status_code
        == 409
    )


def test_listing_unpublish_and_republish_flow():
    employer_id = _user_id("employer@example.com")
    admin_id = _user_id("admin@example.com")

    listing_id = _submit_listing(employer_id, title="Unpublish Test Role").json()[
        "listing_id"
    ]

    # Cannot unpublish a pending listing
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/unpublish", json={"actor_id": admin_id}
        ).status_code
        == 409
    )

    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id}
        ).status_code
        == 200
    )

    # Non-admin cannot unpublish
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/unpublish",
            json={"actor_id": employer_id},
        ).status_code
        == 403
    )

    unpublished = client.post(
        f"/api/admin/listings/{listing_id}/unpublish", json={"actor_id": admin_id}
    )
    assert unpublished.status_code == 200
    assert unpublished.json()["listing"]["status"] == "unpublished"
    assert all(job["id"] != listing_id for job in client.get("/api/jobs").json()["jobs"])

    # Admin can republish an unpublished listing
    republished = client.post(
        f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id}
    )
    assert republished.status_code == 200
    assert any(job["id"] == listing_id for job in client.get("/api/jobs").json()["jobs"])


def test_listing_extend_deadline_reopens_expired_and_validates():
    employer_id = _user_id("employer@example.com")
    admin_id = _user_id("admin@example.com")

    listing_id = _submit_listing(
        employer_id, title="Extendable Role", deadline="2020-05-01"
    ).json()["listing_id"]
    client.post(f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id})

    # Expired listing is hidden
    assert all(job["id"] != listing_id for job in client.get("/api/jobs").json()["jobs"])

    # Invalid date or non-later deadline -> 400; non-admin -> 403
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/deadline",
            json={"deadline": "bad-date", "actor_id": admin_id},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/deadline",
            json={"deadline": "2020-05-01", "actor_id": admin_id},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/deadline",
            json={"deadline": "2099-05-01", "actor_id": employer_id},
        ).status_code
        == 403
    )

    # Valid extension reopens the listing
    extended = client.post(
        f"/api/admin/listings/{listing_id}/deadline",
        json={"deadline": "2099-05-01", "actor_id": admin_id},
    )
    assert extended.status_code == 200
    assert extended.json()["listing"]["deadline"] == "2099-05-01"
    assert any(job["id"] == listing_id for job in client.get("/api/jobs").json()["jobs"])

    # Filled listing cannot be extended
    assert (
        client.post(
            f"/api/employer/listings/{listing_id}/filled",
            json={"actor_id": employer_id},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/deadline",
            json={"deadline": "2099-12-31", "actor_id": admin_id},
        ).status_code
        == 409
    )


def test_listing_mark_filled_by_owner_only():
    employer_id = _user_id("employer@example.com")
    other_employer_id = _user_id("employer2@example.com")
    admin_id = _user_id("admin@example.com")

    listing_id = _submit_listing(employer_id, title="Fillable Role").json()["listing_id"]

    # Cannot mark pending listing as filled
    assert (
        client.post(
            f"/api/employer/listings/{listing_id}/filled",
            json={"actor_id": employer_id},
        ).status_code
        == 409
    )

    client.post(f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id})

    # Non-owner and admin cannot mark filled
    assert (
        client.post(
            f"/api/employer/listings/{listing_id}/filled",
            json={"actor_id": other_employer_id},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/employer/listings/{listing_id}/filled",
            json={"actor_id": admin_id},
        ).status_code
        == 403
    )

    # Owner marks filled -> hidden from graduates
    filled = client.post(
        f"/api/employer/listings/{listing_id}/filled",
        json={"actor_id": employer_id},
    )
    assert filled.status_code == 200
    assert filled.json()["listing"]["status"] == "filled"
    assert all(job["id"] != listing_id for job in client.get("/api/jobs").json()["jobs"])


def test_graduate_subscription_eligibility():
    dcit_grad = _user_id("grad.dcit@example.com")
    other_grad = _user_id("grad.other@example.com")
    employer_id = _user_id("employer@example.com")

    # Eligible DCIT graduate can subscribe (idempotent)
    assert (
        client.post("/api/graduates/subscribe", json={"actor_id": dcit_grad}).status_code
        == 200
    )
    assert (
        client.post("/api/graduates/subscribe", json={"actor_id": dcit_grad}).status_code
        == 200
    )

    # Ineligible graduate and non-graduates are rejected with 403
    assert (
        client.post(
            "/api/graduates/subscribe", json={"actor_id": other_grad}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/graduates/subscribe", json={"actor_id": employer_id}
        ).status_code
        == 403
    )

    # Unknown user returns 404
    assert (
        client.post("/api/graduates/subscribe", json={"actor_id": 999999}).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Notifications scenario tests
# ---------------------------------------------------------------------------


def _reset_preferences(user_id: int) -> None:
    with Session(engine) as session:
        session.exec(
            delete(NotificationPreference).where(
                NotificationPreference.user_id == user_id
            )
        )
        session.commit()


def test_publish_automatically_notifies_subscribers_in_app_and_email(monkeypatch):
    employer_id = _user_id("employer@example.com")
    admin_id = _user_id("admin@example.com")
    grad_id = _user_id("grad.dcit@example.com")
    _reset_preferences(grad_id)

    sent_emails = []

    class CaptureSender:
        def send(self, message):
            sent_emails.append(message)

    monkeypatch.setattr("app.services.notifications.get_email_sender", lambda: CaptureSender())

    listing_id = _submit_listing(employer_id, title="Auto-Notify Role").json()[
        "listing_id"
    ]
    published = client.post(
        f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id}
    )
    assert published.status_code == 200
    assert published.json()["notified"] >= 1

    notifs = client.get("/api/notifications", params={"actor_id": grad_id}).json()[
        "notifications"
    ]
    assert any(
        n["kind"] == "new_listing" and "Auto-Notify Role" in n["message"]
        for n in notifs
    )
    assert any(
        m.to == "grad.dcit@example.com" and "Auto-Notify Role" in m.subject
        for m in sent_emails
    )


def test_manual_notify_subscribers_requires_admin_and_published():
    employer_id = _user_id("employer@example.com")
    admin_id = _user_id("admin@example.com")

    pending_id = _submit_listing(employer_id, title="Pending Notify Check").json()[
        "listing_id"
    ]

    # Non-admin -> 403
    assert (
        client.post(
            f"/api/admin/listings/{pending_id}/notify", json={"actor_id": employer_id}
        ).status_code
        == 403
    )

    # Pending listing -> 409
    assert (
        client.post(
            f"/api/admin/listings/{pending_id}/notify", json={"actor_id": admin_id}
        ).status_code
        == 409
    )

    # Published listing -> 200 with subscriber count
    published_id = _listing_id("Junior Software Engineer")
    notified = client.post(
        f"/api/admin/listings/{published_id}/notify", json={"actor_id": admin_id}
    )
    assert notified.status_code == 200
    assert notified.json()["count"] >= 1


def test_status_transition_notifies_applicant_and_respects_email_preferences(monkeypatch):
    grad_id = _user_id("grad.dcit@example.com")
    employer_id = _user_id("employer@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    sent_emails = []

    class CaptureSender:
        def send(self, message):
            sent_emails.append(message)

    monkeypatch.setattr("app.services.notifications.get_email_sender", lambda: CaptureSender())

    try:
        # Turn off application_status emails for grad.dcit
        pref_off = client.put(
            "/api/graduates/notification-preferences",
            json={
                "actor_id": grad_id,
                "kind": "application_status",
                "email": False,
            },
        )
        assert pref_off.status_code == 200
        assert pref_off.json()["email"]["application_status"] is False

        applied = _apply(grad_id, listing_id, "Notification preference test")
        assert applied.status_code == 200
        application_id = applied.json()["application_id"]

        # Review -> in-app notification created, but no email sent
        reviewed = client.post(
            f"/api/applications/{application_id}/transition",
            json={"action": "review", "actor_id": employer_id},
        )
        assert reviewed.status_code == 200
        assert len(sent_emails) == 0

        notifs = client.get("/api/notifications", params={"actor_id": grad_id}).json()[
            "notifications"
        ]
        assert any(
            n["kind"] == "application_status"
            and n["application_id"] == application_id
            and "reviewed" in n["message"]
            for n in notifs
        )

        # Turn application_status emails back on and reject with reason
        pref_on = client.put(
            "/api/graduates/notification-preferences",
            json={
                "actor_id": grad_id,
                "kind": "application_status",
                "email": True,
            },
        )
        assert pref_on.status_code == 200

        rejected = client.post(
            f"/api/applications/{application_id}/transition",
            json={
                "action": "reject",
                "reason": "Role filled internally",
                "actor_id": employer_id,
            },
        )
        assert rejected.status_code == 200
        assert len(sent_emails) == 1
        assert sent_emails[0].to == "grad.dcit@example.com"
        assert "Role filled internally" in sent_emails[0].body
    finally:
        _reset_preferences(grad_id)


def test_notifications_list_isolation_and_unknown_user():
    grad_dcit = _user_id("grad.dcit@example.com")
    grad_other = _user_id("grad.other@example.com")

    with Session(engine) as session:
        session.add(
            Notification(
                user_id=grad_other,
                message="Private notice for grad.other",
                kind="application_status",
            )
        )
        session.commit()

    dcit_notifs = client.get(
        "/api/notifications", params={"actor_id": grad_dcit}
    ).json()["notifications"]
    other_notifs = client.get(
        "/api/notifications", params={"actor_id": grad_other}
    ).json()["notifications"]

    assert any("Private notice for grad.other" in n["message"] for n in other_notifs)
    assert all("Private notice for grad.other" not in n["message"] for n in dcit_notifs)

    assert client.get("/api/notifications", params={"actor_id": 999999}).status_code == 404


def test_notification_preferences_get_put_and_validation():
    grad_id = _user_id("grad.dcit@example.com")
    employer_id = _user_id("employer@example.com")
    _reset_preferences(grad_id)

    try:
        defaults = client.get(
            "/api/graduates/notification-preferences", params={"actor_id": grad_id}
        )
        assert defaults.status_code == 200
        assert defaults.json()["email"] == {
            "application_status": True,
            "new_listing": True,
        }

        updated = client.put(
            "/api/graduates/notification-preferences",
            json={"actor_id": grad_id, "kind": "new_listing", "email": False},
        )
        assert updated.status_code == 200
        assert updated.json()["email"] == {
            "application_status": True,
            "new_listing": False,
        }

        # Invalid kind -> 400
        bad_kind = client.put(
            "/api/graduates/notification-preferences",
            json={"actor_id": grad_id, "kind": "invalid_kind", "email": False},
        )
        assert bad_kind.status_code == 400

        # Non-graduate -> 403
        assert (
            client.get(
                "/api/graduates/notification-preferences",
                params={"actor_id": employer_id},
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/graduates/notification-preferences",
                json={"actor_id": employer_id, "kind": "new_listing", "email": False},
            ).status_code
            == 403
        )

        # Unknown user -> 404
        assert (
            client.get(
                "/api/graduates/notification-preferences", params={"actor_id": 999999}
            ).status_code
            == 404
        )
        assert (
            client.put(
                "/api/graduates/notification-preferences",
                json={"actor_id": 999999, "kind": "new_listing", "email": False},
            ).status_code
            == 404
        )
    finally:
        _reset_preferences(grad_id)


# ---------------------------------------------------------------------------
# Admin Data & access-control scenario tests
# ---------------------------------------------------------------------------


def test_admin_pending_listings_access_control():
    admin_id = _user_id("admin@example.com")
    employer_id = _user_id("employer@example.com")
    grad_id = _user_id("grad.dcit@example.com")

    ok = client.get("/api/admin/listings/pending", params={"actor_id": admin_id})
    assert ok.status_code == 200
    assert any(item["title"] == "QA Intern" for item in ok.json()["listings"])

    for forbidden_id in (employer_id, grad_id, 999999):
        res = client.get(
            "/api/admin/listings/pending", params={"actor_id": forbidden_id}
        )
        assert res.status_code == 403


def test_listing_applicants_access_control():
    grad_id = _user_id("grad.dcit@example.com")
    owner_id = _user_id("employer@example.com")
    other_employer_id = _user_id("employer2@example.com")
    admin_id = _user_id("admin@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    applied = _apply(grad_id, listing_id, "Applicant visibility test")
    assert applied.status_code == 200
    app_id = applied.json()["application_id"]

    # Owning employer and admin can view applicants
    owner_view = client.get(
        f"/api/listings/{listing_id}/applicants", params={"actor_id": owner_id}
    )
    assert owner_view.status_code == 200
    assert any(a["id"] == app_id for a in owner_view.json()["applicants"])

    admin_view = client.get(
        f"/api/listings/{listing_id}/applicants", params={"actor_id": admin_id}
    )
    assert admin_view.status_code == 200
    assert any(a["id"] == app_id for a in admin_view.json()["applicants"])

    # Non-owning employer and graduate are blocked (403)
    assert (
        client.get(
            f"/api/listings/{listing_id}/applicants",
            params={"actor_id": other_employer_id},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/listings/{listing_id}/applicants", params={"actor_id": grad_id}
        ).status_code
        == 403
    )

    # Unknown user or missing listing -> 404
    assert (
        client.get(
            f"/api/listings/{listing_id}/applicants", params={"actor_id": 999999}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/listings/999999/applicants", params={"actor_id": admin_id}
        ).status_code
        == 404
    )


def test_admin_export_applicants_package_and_access_control():
    grad_id = _user_id("grad.dcit@example.com")
    owner_id = _user_id("employer@example.com")
    other_employer_id = _user_id("employer2@example.com")
    admin_id = _user_id("admin@example.com")
    listing_id = _listing_id("Junior Software Engineer")

    applied = _apply(grad_id, listing_id, "Export package cover letter")
    assert applied.status_code == 200
    app_id = applied.json()["application_id"]

    # Admin can export JSON applicant package
    exported = client.get(
        f"/api/admin/listings/{listing_id}/export", params={"actor_id": admin_id}
    )
    assert exported.status_code == 200
    body = exported.json()
    assert body["ok"] is True
    assert body["format"] == "json"
    record = next(item for item in body["package"] if item["id"] == app_id)
    assert record["cover_letter"] == "Export package cover letter"
    assert record["status"] == "submitted"

    # Non-admins (including owning employer) cannot export
    for forbidden_id in (owner_id, other_employer_id, grad_id, 999999):
        res = client.get(
            f"/api/admin/listings/{listing_id}/export",
            params={"actor_id": forbidden_id},
        )
        assert res.status_code == 403

    # Missing listing returns 404 for admin
    assert (
        client.get(
            "/api/admin/listings/999999/export", params={"actor_id": admin_id}
        ).status_code
        == 404
    )


def test_application_history_access_control():
    grad_id = _user_id("grad.dcit@example.com")
    owner_id = _user_id("employer@example.com")
    other_employer_id = _user_id("employer2@example.com")
    admin_id = _user_id("admin@example.com")

    listing_id = _submit_listing(owner_id, title="History Access Listing").json()[
        "listing_id"
    ]
    assert (
        client.post(
            f"/api/admin/listings/{listing_id}/publish", json={"actor_id": admin_id}
        ).status_code
        == 200
    )

    applied = _apply(grad_id, listing_id, "History access test")
    assert applied.status_code == 200
    app_id = applied.json()["application_id"]

    client.post(
        f"/api/applications/{app_id}/transition",
        json={"action": "review", "actor_id": owner_id},
    )

    # Admin and owning employer can view history
    admin_hist = client.get(
        f"/api/applications/{app_id}/history", params={"actor_id": admin_id}
    )
    assert admin_hist.status_code == 200
    assert len(admin_hist.json()["history"]) == 1

    owner_hist = client.get(
        f"/api/applications/{app_id}/history", params={"actor_id": owner_id}
    )
    assert owner_hist.status_code == 200
    assert len(owner_hist.json()["history"]) == 1

    # Non-owning employer and graduate are blocked (403)
    assert (
        client.get(
            f"/api/applications/{app_id}/history",
            params={"actor_id": other_employer_id},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/applications/{app_id}/history", params={"actor_id": grad_id}
        ).status_code
        == 403
    )

    # Missing application -> 404
    assert (
        client.get(
            "/api/applications/999999/history", params={"actor_id": admin_id}
        ).status_code
        == 404
    )
