"""Identity values at the trusted server boundary, never request authentication."""
from dataclasses import dataclass, field
from datetime import datetime
import re
from uuid import UUID


class IdentityRejected(Exception):
    def __init__(self, code="IDENTITY_REJECTED"):
        self.code = code
        super().__init__(code)


class AuthorizationDenied(IdentityRejected):
    pass


@dataclass(frozen=True)
class Principal:
    """03b's verified session adapter supplies this; no login/issuer exists here.

    Constructing this internal value is not authentication. Never deserialize it
    from a request, header, cookie payload or caller-provided owner UUID.
    """
    user_id: UUID
    role: str
    security_epoch: int
    session_id: UUID
    device_id: UUID
    mfa_level: str
    reauthenticated_at: datetime | None
    expires_at: datetime

    def __post_init__(self):
        if any(not isinstance(v, UUID) or v.version != 4
               for v in (self.user_id, self.session_id, self.device_id)):
            raise ValueError("principal identifiers must be UUIDv4")
        if self.role not in ("student", "admin") or type(self.security_epoch) is not int or self.security_epoch < 1:
            raise ValueError("invalid principal role or epoch")
        if self.expires_at.tzinfo is None or (self.reauthenticated_at is not None and self.reauthenticated_at.tzinfo is None):
            raise ValueError("principal timestamps must include a timezone")


@dataclass(frozen=True)
class IssuedInvitation:
    id: UUID
    token: str = field(repr=False)
    expires_at: datetime


def normalize_email(value: str) -> str:
    """Preserve local part, dots and plus; normalize only domain case/IDNA."""
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        raise IdentityRejected("INVALID_INPUT")
    local, domain = value.split("@")
    if not local or len(local) > 64 or any(ch.isspace() or ord(ch) < 33 for ch in value):
        raise IdentityRejected("INVALID_INPUT")
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise IdentityRejected("INVALID_INPUT") from None
    labels = domain.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise IdentityRejected("INVALID_INPUT")
    result = local + "@" + domain
    if len(result) > 254:
        raise IdentityRejected("INVALID_INPUT")
    return result
