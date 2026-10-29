"""Persistent execution values. No database, provider SDK or HTTP dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from uuid import UUID


KINDS = frozenset(('asr_live', 'asr_retranscribe', 'ai_question', 'ai_explanation',
                   'review_summary', 'export', 'import', 'delete'))
STATES = frozenset(('queued', 'running', 'succeeded', 'failed', 'unknown',
                    'cancel_requested', 'cancelled'))


class StaleFence(Exception):
    """Lease expired, was superseded, or belongs to a different worker."""


class IdempotencyMismatch(Exception):
    """409 IDEMPOTENCY_MISMATCH: same operation key, different canonical request."""


class ResultConflict(Exception):
    """Duplicate result has different bytes; preserve the first result."""


class UnsafeRetry(Exception):
    """A new charged attempt requires a separate acknowledgement and budget hold."""


class ResyncRequired(Exception):
    """Cursor is foreign, invalid, ahead, or outside retained event history."""


def fingerprint(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > 16384:
        raise ValueError('operation metadata exceeds limit')
    return hashlib.sha256(encoded).hexdigest()


def input_reference(value: dict) -> dict:
    """Only implemented immutable ID references; never code, URLs or raw content."""
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value.get('schema_version') != 1):
        raise ValueError('input_ref schema_version must be 1')
    allowed = {'schema_version', 'class_session_id', 'course_material_version_id'}
    if set(value) - allowed:
        raise ValueError('unsupported task input reference')
    result: dict[str, object] = {'schema_version': 1}
    for key in set(value) - {'schema_version'}:
        reference = UUID(str(value[key]))
        if reference.version != 4:
            raise ValueError('reference must be UUIDv4')
        result[key] = str(reference)
    return result


def validate_json_output(text: str) -> dict:
    """Generic structural validation; content-specific schemas belong to producers."""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON output key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('non-finite JSON output')

    result = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError('provider output must be an object')
    return result


@dataclass(frozen=True, kw_only=True)
class Task:
    id: UUID
    created_at: datetime
    owner_id: UUID
    class_session_id: UUID | None
    parent_task_id: UUID | None
    kind: str
    state: str
    input_ref: dict
    schema_version: str
    priority: int
    not_before: datetime
    lease_token: int
    leased_by: str | None
    lease_expires_at: datetime | None
    error_code: str | None
    cancel_requested_at: datetime | None
    completed_at: datetime | None
    plan_id: UUID | None
    revision: int
    updated_at: datetime
    may_have_charged: bool


@dataclass(frozen=True, kw_only=True)
class Attempt:
    id: UUID
    created_at: datetime
    owner_id: UUID
    task_id: UUID
    attempt_number: int
    state: str
    provider: str | None
    provider_request_id: str | None
    model: str | None
    price_revision: str | None
    submitted_at: datetime | None
    finished_at: datetime | None
    raw_output_ref: UUID | None
    provider_outcome: str | None
    error_code: str | None
    duplicate_billing_ack: bool


@dataclass(frozen=True, kw_only=True)
class ProviderResult:
    id: UUID
    created_at: datetime
    owner_id: UUID
    attempt_id: UUID
    raw_output: str | None
    asset_id: UUID | None
    sha256: str
    validation_status: str
    validated_payload: dict | None
    received_at: datetime
    conflict_sha256: str | None
    conflict_detected_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class Claim:
    id: UUID
    owner_id: UUID
    kind: str
    lease_token: int
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class ReplayPage:
    events: list[dict]
    cursor: str
