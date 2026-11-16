"""Formal PR-05a identity/PG candidate. Runtime injection is disabled by default."""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from ..domain.auth import AuthRejected, AuthReply
from ..infrastructure.auth_crypto import digest

SCOPES = {"learning": "learning", "drafts": "draft", "recording_recovery": "recording"}


def canonical_uuid(value):
    try:
        result = UUID(value)
        if result.version != 4 or str(result) != value:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise AuthRejected("VALIDATION_FAILED", 422) from None


class CacheKeyService:
    def __init__(self, *, auth, repository, crypto, enabled=False, allow_recovery=False,
                 recording_authorizer=None):
        self.auth = auth
        self.repository = repository
        self.crypto = crypto
        self.enabled = enabled
        self.allow_recovery = allow_recovery
        self.recording_authorizer = recording_authorizer

    def release(self, context, body, operation_key, *, recovery=False):
        self.auth.require_csrf(context, "session")
        principal = self.auth.principal(context)
        if not self.enabled or (recovery and not self.allow_recovery):
            raise AuthRejected("SERVICE_DISABLED", 503)
        required = {"device_id", "key_epoch", "scope", "class_session_id"}
        if recovery:
            required.add("key_id")
        if not isinstance(body, dict) or set(body) != required:
            raise AuthRejected("VALIDATION_FAILED", 422)
        device = canonical_uuid(body["device_id"])
        if device != principal.device_id:
            raise AuthRejected("FORBIDDEN", 403)
        if type(body["key_epoch"]) is not int or body["key_epoch"] < 1 or not isinstance(body["scope"], str) or body["scope"] not in SCOPES:
            raise AuthRejected("VALIDATION_FAILED", 422)
        classroom = canonical_uuid(body["class_session_id"]) if body["class_session_id"] is not None else None
        if (body["scope"] == "recording_recovery") != (classroom is not None):
            raise AuthRejected("VALIDATION_FAILED", 422)
        if not context.reauth:
            raise AuthRejected("REAUTH_REQUIRED", 403)
        canonical_uuid(operation_key)
        key_id = canonical_uuid(body["key_id"]) if recovery else None
        purpose = "recoverCache" if recovery else "unlockCache"
        operation = dict(purpose=purpose, target=dict(kind="device", id=str(device)),
                         candidate_parameters=dict(idempotency_key=operation_key, body=body))
        authorization = dict(owner_id=str(principal.user_id), session_id=str(principal.session_id),
                             device_id=str(device), security_epoch=principal.security_epoch,
                             role=principal.role, key_epoch=body["key_epoch"], scope=body["scope"],
                             purpose=purpose, operation_key=operation_key,
                             reauth_hash=digest("reauth", context.reauth),
                             commitment=self.auth.crypto.commitment("operation", operation))
        with self.repository.authorized(principal, authorization) as (tx, grant):
            if classroom is not None:
                # Catalog lookup enforces the existing owner/ancestor/tombstone chain.
                tx.catalog.get_class_session(classroom)
                if self.recording_authorizer is None:
                    raise AuthRejected("SERVICE_DISABLED", 503)
                self.recording_authorizer.authorize(tx.connection, principal, classroom)
            metadata = dict(id=uuid4(), owner_id=principal.user_id, device_id=device,
                            purpose=SCOPES[body["scope"]], class_session_id=classroom,
                            key_version=body["key_epoch"])
            if recovery:
                row = self.repository.historical(tx.connection, metadata, key_id)
            else:
                row = self.repository.get_or_create(tx.connection, metadata, self.crypto)
            if row is None or row["revoked_at"] is not None:
                raise AuthRejected("FORBIDDEN", 403)
            material = self.crypto.unwrap(row)
            expires = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
            if tx.connection.scalar(sa.text("SELECT clock_timestamp()")) >= expires:
                raise AuthRejected("REAUTH_REQUIRED", 403)
            response = dict(key_id=str(row["id"]), key_epoch=body["key_epoch"], key_material_b64=material,
                            expires_at=expires.isoformat(), scope=body["scope"],
                            cache_policy_revision=grant["policy_revision"])
            if recovery:
                response["key_version"] = row["key_version"]
        # An exception/uncertain COMMIT above prevents any secret response from escaping.
        return AuthReply(response)
