"""Scenario smoke and lifecycle integration tests."""

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.database import engine
from app.models.application import Application
from app.models.listing import Listing
from app.models.user import User
from app.main import app
from app.seed import seed


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
