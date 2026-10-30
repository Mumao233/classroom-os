"""Pure identity input boundaries and mature Argon2id verification."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from argon2 import PasswordHasher
import pytest

from classroom_web.domain.identity import (
    IdentityRejected, IssuedInvitation, Principal, normalize_email,
)
from classroom_web.infrastructure.identity_passwords import Argon2PasswordHasher


def test_domain_normalization_preserves_local_identity_dots_plus_and_case():
    assert normalize_email("Name.Dot+tag@EXAMPLE.TEST") == "Name.Dot+tag@example.test"
    assert normalize_email("Name.Dot+tag@example.test") != normalize_email("name.dot+tag@example.test")
    assert normalize_email("Name@bücher.example") == "Name@xn--bcher-kva.example"


@pytest.mark.parametrize("value", [None, "", "missing-domain", "a@@example.test", "@example.test",
                                  " a@example.test", "a@example.test\n", "a@-bad.example",
                                  "a@example..test", "a@localhost", "x" * 65 + "@example.test"])
def test_invalid_emails_have_one_safe_error_without_echo(value):
    with pytest.raises(IdentityRejected) as caught:
        normalize_email(value)
    assert str(caught.value) == "INVALID_INPUT"


@pytest.mark.parametrize("password", ["x" * 11, "x" * 1025, None, 123])
def test_password_wire_length_bounds_are_enforced(password):
    with pytest.raises(IdentityRejected):
        Argon2PasswordHasher().hash(password)


@pytest.mark.parametrize("password", ["x" * 12, "x" * 1024])
def test_argon2id_hash_is_salted_and_verifies_with_mature_component(password):
    hasher = Argon2PasswordHasher()
    first, second = hasher.hash(password), hasher.hash(password)
    assert first.startswith("$argon2id$") and second.startswith("$argon2id$")
    assert first != second
    assert PasswordHasher().verify(first, password)
    assert PasswordHasher().verify(second, password)


def valid_principal():
    now = datetime.now(timezone.utc)
    return Principal(user_id=uuid4(), role="student", security_epoch=1,
                     session_id=uuid4(), device_id=uuid4(), mfa_level="none",
                     reauthenticated_at=None, expires_at=now + timedelta(minutes=10))


@pytest.mark.parametrize("field,value", [
    ("user_id", UUID(int=0)), ("session_id", UUID(int=0)), ("device_id", "client-device"),
    ("role", "superadmin"), ("security_epoch", 0), ("security_epoch", True),
    ("expires_at", datetime(2026, 10, 30)), ("reauthenticated_at", datetime(2026, 10, 30)),
])
def test_principal_boundary_rejects_malformed_internal_evidence(field, value):
    with pytest.raises(ValueError):
        replace(valid_principal(), **{field: value})


def test_invitation_secret_is_not_in_default_diagnostic_repr():
    token = "synthetic-secret-never-a-diagnostic"
    invitation = IssuedInvitation(uuid4(), token, datetime.now(timezone.utc))
    assert token not in repr(invitation)
