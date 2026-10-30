"""PR-03a internal use cases. No session, HTTP login or admin bootstrap."""
from datetime import timedelta
import hashlib
import secrets

from ..domain.identity import IdentityRejected, IssuedInvitation, normalize_email
from ..domain.identity_ports import IdentityRepository, PasswordHasher, VerificationMail


def token_digest(token: str) -> bytes:
    if not isinstance(token, str) or not 24 <= len(token) <= 256:
        raise IdentityRejected()
    return hashlib.sha256(token.encode("utf-8")).digest()


class IdentityService:
    def __init__(self, repository: IdentityRepository, hasher: PasswordHasher, *,
                 admin_reauthentication_window: timedelta,
                 resend_interval: timedelta = timedelta(seconds=60)):
        if admin_reauthentication_window <= timedelta(0) or resend_interval < timedelta(0):
            raise ValueError("explicit positive admin window and nonnegative resend interval required")
        self.repository = repository
        self.hasher = hasher
        self.admin_window = admin_reauthentication_window
        self.resend_interval = resend_interval

    def create_invitation(self, admin, *, kind="single_use", email=None):
        if kind not in ("single_use", "email_bound") or (kind == "email_bound") != (email is not None):
            raise IdentityRejected("INVALID_INPUT")
        token = secrets.token_urlsafe(32)
        result = self.repository.create_invitation(admin, normalize_email(email) if email is not None else None,
                                                   token_digest(token), self.admin_window)
        return IssuedInvitation(result[0], token, result[1])

    def revoke_invitation(self, admin, invitation_id):
        return self.repository.revoke_invitation(admin, invitation_id, self.admin_window)

    def set_capacity(self, admin, capacity):
        if type(capacity) is not int or capacity not in (10, 30):
            raise IdentityRejected("INVALID_INPUT")
        return self.repository.set_capacity(admin, capacity, self.admin_window)

    def register(self, email, password, invitation_token, nickname=None):
        email = normalize_email(email)
        if nickname is not None and (not isinstance(nickname, str) or len(nickname) > 100):
            raise IdentityRejected("INVALID_INPUT")
        # Validation remains in the use case even with a substituted hasher.
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise IdentityRejected("INVALID_INPUT")
        invite_digest = token_digest(invitation_token)
        return self.repository.register(email, self.hasher.hash(password), invite_digest,
                                        token_digest(secrets.token_urlsafe(32)),
                                        {} if nickname is None else {"nickname": nickname})

    def resend(self, email, invitation_token=None):
        self.repository.resend(normalize_email(email), token_digest(invitation_token) if invitation_token is not None else None,
                               token_digest(secrets.token_urlsafe(32)), self.resend_interval)

    def dispatch_mail(self, adapter: VerificationMail) -> bool:
        token = secrets.token_urlsafe(32)
        digest = token_digest(token)
        claim = self.repository.claim_mail(digest)
        if claim is None:
            return False
        # The committed reference/lease is recoverable. Secrets exist only in
        # worker memory and the explicit capture adapter, never durable outbox.
        adapter.send_verification(claim["normalized_email"], token)
        self.repository.complete_mail(claim, digest)
        return True

    def activate(self, token):
        return self.repository.activate(token_digest(token))
