from types import SimpleNamespace

import jwt
import pytest

from app.models.application import Application
from app.models.listing import Listing
from app.models.user import User
from app.services.application_states import IllegalTransitionError
from app.services.auth_service import AuthService
from app.services.employer import Employer
from app.services.errors import ForbiddenError
from app.services.job_application import JobApplication
from app.utilities.security import encrypt_password, verify_password


def test_password_hash_round_trip_and_rejects_wrong_password():
    password_hash = encrypt_password("correct horse")

    assert password_hash != "correct horse"
    assert verify_password("correct horse", password_hash)
    assert not verify_password("wrong horse", password_hash)


def test_authenticate_user_returns_token_for_valid_credentials(monkeypatch):
    user = SimpleNamespace(
        id=7,
        username="alice",
        password=encrypt_password("secret"),
        role="regular_user",
    )

    class FakeUserRepository:
        def get_by_username(self, username):
            return user if username == "alice" else None

    monkeypatch.setattr(
        "app.services.auth_service.create_access_token",
        lambda data: {"sub": data["sub"], "role": data["role"]},
    )

    token = AuthService(FakeUserRepository()).authenticate_user("alice", "secret")

    assert token == {"sub": "7", "role": "regular_user"}


def test_authenticate_user_returns_none_for_invalid_credentials():
    user = SimpleNamespace(
        id=7,
        username="alice",
        password=encrypt_password("secret"),
        role="regular_user",
    )

    class FakeUserRepository:
        def get_by_username(self, username):
            return user if username == "alice" else None

    service = AuthService(FakeUserRepository())

    assert service.authenticate_user("alice", "incorrect") is None
    assert service.authenticate_user("missing", "secret") is None


def test_access_token_contains_subject_and_role():
    from app.config import get_settings
    from app.utilities.security import create_access_token

    token = create_access_token({"sub": "7", "role": "admin"})
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    assert payload["sub"] == "7"
    assert payload["role"] == "admin"
    assert "exp" in payload


def _lifecycle_context(status="submitted", owner_id=10):
    listing = Listing(
        id=1,
        employer_id=owner_id,
        title="Lifecycle Test",
        company="Test Co",
        description="Test",
        apply_mode="collect",
        deadline="2099-12-31",
        status="published",
    )
    candidate = User(
        id=20,
        username="candidate",
        email="candidate@example.com",
        password="hashed",
        role="graduate",
        programme="DCIT",
    )
    application = Application(
        id=30,
        listing_id=1,
        graduate_id=20,
        cover_letter="Cover letter",
        resume_text="Resume",
        status=status,
    )
    return JobApplication(application, listing, candidate)


def test_application_lifecycle_accept_path_and_history():
    application = _lifecycle_context()

    employer = Employer(
        User(
            id=10,
            username="owner",
            email="owner@example.com",
            password="hashed",
            role="employer",
        )
    )

    employer.review_application(application)
    employer.shortlist_application(application)
    employer.accept_application(application)

    assert application.status == "hired"
    assert [change.old_status for change in application.history] == [
        "submitted",
        "reviewed",
        "shortlisted",
    ]
    assert [change.new_status for change in application.history] == [
        "reviewed",
        "shortlisted",
        "hired",
    ]


@pytest.mark.parametrize(
    ("start_status", "action", "reason"),
    [
        ("submitted", "shortlist", None),
        ("submitted", "accept", None),
        ("reviewed", "review", None),
        ("reviewed", "accept", None),
        ("shortlisted", "review", None),
        ("hired", "review", None),
        ("rejected", "reopen", None),
    ],
)
def test_application_lifecycle_rejects_illegal_or_skipped_transitions(
    start_status, action, reason
):
    application = _lifecycle_context(status=start_status)
    employer = Employer(
        User(
            id=10,
            username="owner",
            email="owner@example.com",
            password="hashed",
            role="employer",
        )
    )

    with pytest.raises((IllegalTransitionError, ForbiddenError)):
        employer.perform(action, application, reason)


def test_application_rejection_requires_a_reviewed_or_shortlisted_state():
    employer = Employer(
        User(
            id=10,
            username="owner",
            email="owner@example.com",
            password="hashed",
            role="employer",
        )
    )

    submitted = _lifecycle_context(status="submitted")
    with pytest.raises(IllegalTransitionError):
        employer.reject_application(submitted, "No fit")

    reviewed = _lifecycle_context(status="reviewed")
    employer.reject_application(reviewed, "No fit")
    assert reviewed.status == "rejected"
    assert reviewed.rejection_reason == "No fit"


def test_application_lifecycle_blocks_employer_who_does_not_own_listing():
    application = _lifecycle_context(owner_id=10)
    other_employer = Employer(
        User(
            id=11,
            username="other-owner",
            email="other-owner@example.com",
            password="hashed",
            role="employer",
        )
    )

    with pytest.raises(ForbiddenError):
        other_employer.review_application(application)

    assert application.status == "submitted"
