from datetime import date, timedelta
from types import SimpleNamespace

import jwt
import pytest

from app.models.application import Application
from app.models.listing import Listing
from app.models.user import User
from app.services.application_states import IllegalTransitionError
from app.services.auth_service import AuthService
from app.services.board_auth import public_user
from app.services.email import EmailMessage
from app.services.employer import Employer
from app.services.errors import ForbiddenError
from app.services.job_application import JobApplication
from app.services.listings import (
    _validate_listing_fields,
    check_eligible_graduate,
    is_eligible,
    is_open,
)
from app.services.notifications import (
    APPLICATION_STATUS,
    NEW_LISTING,
    ApplicantNotifier,
    deliver_emails,
    status_message,
    wants_email,
)
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


# ---------------------------------------------------------------------------
# Authentication unit tests
# ---------------------------------------------------------------------------


def test_public_user_strips_password_fields_from_model_and_dict():
    user = User(
        id=5,
        username="grad",
        email="grad@example.com",
        password=encrypt_password("secret"),
        role="graduate",
        programme="DCIT",
    )

    from_model = public_user(user)
    assert from_model is not None
    assert "password" not in from_model
    assert "password_hash" not in from_model
    assert from_model["email"] == "grad@example.com"

    from_dict = public_user(
        {
            "id": 5,
            "email": "grad@example.com",
            "password": "secret",
            "password_hash": "hash",
        }
    )
    assert from_dict == {"id": 5, "email": "grad@example.com"}
    assert public_user(None) is None


# ---------------------------------------------------------------------------
# Listings unit tests
# ---------------------------------------------------------------------------


def test_is_eligible_and_check_eligible_graduate():
    dcit_grad = {"id": 1, "role": "graduate", "programme": "DCIT"}
    other_grad = {"id": 2, "role": "graduate", "programme": "OTHER"}
    employer = {"id": 3, "role": "employer", "programme": None}
    admin = {"id": 4, "role": "admin", "programme": None}

    assert is_eligible(dcit_grad) is True
    assert check_eligible_graduate(dcit_grad) is None

    assert is_eligible(other_grad) is False
    other_err = check_eligible_graduate(other_grad)
    assert other_err is not None and other_err["status_code"] == 403

    assert is_eligible(employer) is False
    emp_err = check_eligible_graduate(employer)
    assert emp_err is not None and emp_err["status_code"] == 403

    assert is_eligible(admin) is False
    admin_err = check_eligible_graduate(admin)
    assert admin_err is not None and admin_err["status_code"] == 403

    assert is_eligible(None) is False
    none_err = check_eligible_graduate(None)
    assert none_err is not None and none_err["status_code"] == 404


def test_is_open_checks_published_status_and_deadline():
    today = date.today()
    future = (today + timedelta(days=5)).isoformat()
    past = (today - timedelta(days=1)).isoformat()

    def make_listing(status: str, deadline: str) -> Listing:
        return Listing(
            employer_id=1,
            title="Role",
            company="Co",
            description="Desc",
            apply_mode="collect",
            deadline=deadline,
            status=status,
        )

    assert is_open(make_listing("published", future)) is True
    assert is_open(make_listing("published", today.isoformat())) is True
    assert is_open(make_listing("published", past)) is False
    for non_published in ("pending", "rejected", "unpublished", "filled"):
        assert is_open(make_listing(non_published, future)) is False


def test_validate_listing_fields():
    assert _validate_listing_fields("collect", "2099-12-31", None) is None
    assert (
        _validate_listing_fields("external", "2099-12-31", "https://example.com/apply")
        is None
    )

    bad_mode = _validate_listing_fields("email", "2099-12-31", None)
    assert bad_mode is not None and bad_mode["status_code"] == 400

    missing_url = _validate_listing_fields("external", "2099-12-31", None)
    assert missing_url is not None and missing_url["status_code"] == 400

    bad_date = _validate_listing_fields("collect", "31-12-2099", None)
    assert bad_date is not None and bad_date["status_code"] == 400


# ---------------------------------------------------------------------------
# Notifications unit tests
# ---------------------------------------------------------------------------


def test_status_message_includes_rejection_reason_only_when_rejected():
    listing = Listing(
        id=1,
        employer_id=10,
        title="Backend Dev",
        company="Acme",
        description="Build APIs",
        apply_mode="collect",
        deadline="2099-12-31",
        status="published",
    )
    reviewed = Application(
        id=1,
        listing_id=1,
        graduate_id=20,
        cover_letter="Hi",
        resume_text="CV",
        status="reviewed",
        rejection_reason="ignored",
    )
    rejected_with_reason = Application(
        id=2,
        listing_id=1,
        graduate_id=20,
        cover_letter="Hi",
        resume_text="CV",
        status="rejected",
        rejection_reason="Missing Python experience",
    )
    rejected_without_reason = Application(
        id=3,
        listing_id=1,
        graduate_id=20,
        cover_letter="Hi",
        resume_text="CV",
        status="rejected",
        rejection_reason=None,
    )

    msg_reviewed = status_message(listing, reviewed)
    assert "Backend Dev" in msg_reviewed and "reviewed" in msg_reviewed
    assert "Reason:" not in msg_reviewed

    msg_rejected = status_message(listing, rejected_with_reason)
    assert "rejected" in msg_rejected
    assert "Reason: Missing Python experience" in msg_rejected

    msg_rejected_plain = status_message(listing, rejected_without_reason)
    assert "rejected" in msg_rejected_plain
    assert "Reason:" not in msg_rejected_plain


def test_wants_email_defaults_to_true_and_respects_preference():
    class FakePrefs:
        def __init__(self, prefs):
            self._prefs = prefs

        def get(self, user_id, kind):
            return self._prefs.get((user_id, kind))

    prefs = FakePrefs(
        {
            (1, APPLICATION_STATUS): SimpleNamespace(email=False),
            (1, NEW_LISTING): SimpleNamespace(email=True),
        }
    )

    assert wants_email(prefs, 1, APPLICATION_STATUS) is False
    assert wants_email(prefs, 1, NEW_LISTING) is True
    assert wants_email(prefs, 2, APPLICATION_STATUS) is True


def test_deliver_emails_skips_none_and_isolates_failures():
    sent = []

    class FlakySender:
        def send(self, message):
            if message.to == "fail@example.com":
                raise RuntimeError("SMTP down")
            sent.append(message.to)

    deliver_emails(
        [
            None,
            EmailMessage(to="first@example.com", subject="s1", body="b1"),
            EmailMessage(to="fail@example.com", subject="s2", body="b2"),
            EmailMessage(to="second@example.com", subject="s3", body="b3"),
        ],
        sender=FlakySender(),
    )

    assert sent == ["first@example.com", "second@example.com"]


def test_applicant_notifier_stages_in_app_and_respects_email_preference():
    saved_notifications = []

    class FakeNotificationRepo:
        db = None

        def add(self, notification):
            saved_notifications.append(notification)
            return notification

    class FakePrefs:
        def __init__(self, email_enabled: bool):
            self.email_enabled = email_enabled

        def get(self, user_id, kind):
            return SimpleNamespace(email=self.email_enabled)

    applicant = User(
        id=20,
        username="grad",
        email="grad@example.com",
        password="hash",
        role="graduate",
        programme="DCIT",
    )
    listing = Listing(
        id=1,
        employer_id=10,
        title="Data Engineer",
        company="DataCo",
        description="ETL",
        apply_mode="collect",
        deadline="2099-12-31",
        status="published",
    )
    application = Application(
        id=30,
        listing_id=1,
        graduate_id=20,
        cover_letter="Hi",
        resume_text="CV",
        status="shortlisted",
    )

    notifier_on = ApplicantNotifier(FakeNotificationRepo(), preferences=FakePrefs(True))
    email_on = notifier_on.stage_status_change(applicant, application, listing)
    assert email_on is not None
    assert email_on.to == "grad@example.com"
    assert "Data Engineer" in email_on.subject
    assert len(saved_notifications) == 1
    assert saved_notifications[0].kind == APPLICATION_STATUS
    assert saved_notifications[0].application_id == 30

    notifier_off = ApplicantNotifier(
        FakeNotificationRepo(), preferences=FakePrefs(False)
    )
    email_off = notifier_off.stage_status_change(applicant, application, listing)
    assert email_off is None
    assert len(saved_notifications) == 2
