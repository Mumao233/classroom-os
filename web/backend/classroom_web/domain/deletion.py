"""Deletion lifecycle values and ports. No database, SDK or HTTP dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Protocol
from uuid import UUID

# Catalog scopes revoke through a tombstone; media scopes revoke on the manifest row.
CATALOG_SCOPES = ('semester', 'course', 'class_session')
MEDIA_SCOPES = ('media_asset', 'audio')
SCOPES = CATALOG_SCOPES + MEDIA_SCOPES + ('account',)
ORIGINS = ('user', 'expiry')
# Upper bound on the descendants one cascade may name; larger cascades are refused.
MAX_MEMBERS = 5000
DTO_STATES = {'access_revoked': 'access_revoked', 'pending': 'pending_delete',
              'failed': 'delete_failed', 'complete': 'deleted'}


class LedgerUnavailable(Exception):
    """The independent ledger could not durably accept or return an entry."""


class LedgerCorrupt(Exception):
    """The ledger chain is broken, has a gap, or disagrees with local state. Stay closed."""


class LedgerConflict(Exception):
    """The same intent identity was recorded with different content."""


class ScopeExpanded(Exception):
    """This database holds descendants under a cascade's root that the accepted deletion
    did not cover. Applying it would delete more than the user confirmed. Stay closed."""


class StaleDeletionFence(Exception):
    """Another worker owns the request now; this worker must stop without recording."""


class SettleWindowOpen(Exception):
    """An absence proof was attempted while an earlier upload could still land."""


@dataclass(frozen=True)
class DeletionPolicy:
    """Technical bounds only. Retention periods, the physical deadline and backup
    retention are product decisions (TBD-04, TBD-05) and have no default here."""
    # Longest time an upload admitted before revocation may still write its object:
    # the 60 second media ingress budget plus storage timeouts, with margin.
    write_settle: timedelta = timedelta(seconds=120)
    retry_backoff: timedelta = timedelta(seconds=60)
    max_backoff: timedelta = timedelta(hours=1)
    lease_seconds: int = 120
    confirmation_lifetime: timedelta = timedelta(minutes=5)
    # An object no manifest row names is removed only after this age.
    orphan_grace: timedelta = timedelta(hours=24)
    # None until TBD-04 approves a physical deletion target; never invented here.
    physical_deadline: timedelta | None = None

    def __post_init__(self):
        if (not timedelta(0) <= self.write_settle <= timedelta(hours=1)
                or not timedelta(0) <= self.retry_backoff <= self.max_backoff <= timedelta(days=1)
                or type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 3600
                or not timedelta(seconds=1) <= self.confirmation_lifetime <= timedelta(minutes=30)
                or not timedelta(0) <= self.orphan_grace <= timedelta(days=30)
                or (self.physical_deadline is not None and self.physical_deadline <= timedelta(0))):
            raise ValueError('invalid deletion policy')

    def backoff(self, attempt: int) -> int:
        seconds = self.retry_backoff.total_seconds() * (2 ** max(0, min(attempt - 1, 16)))
        return int(min(seconds, self.max_backoff.total_seconds()))


def _derived(label: str, *parts: object) -> UUID:
    digest = hashlib.sha256('\x1f'.join([label, *map(str, parts)]).encode('utf-8')).digest()
    return UUID(bytes=digest[:16], version=4)


@dataclass(frozen=True)
class DeletionIntent:
    """Minimal replayable identifiers; never a title, file name, hash or other content."""
    intent_id: UUID
    request_id: UUID
    owner_id: UUID
    scope_type: str
    scope_id: UUID
    origin: str
    # Classrooms a course or semester cascade covered when it was accepted. A restored
    # copy may hold them under another parent, so replay cannot derive them again.
    members: tuple = ()
    # Courses a semester cascade covered, for the same reason.
    courses: tuple = ()

    def __post_init__(self):
        if (self.scope_type not in SCOPES or self.origin not in ORIGINS
                or type(self.members) is not tuple or type(self.courses) is not tuple
                or len(self.members) + len(self.courses) > MAX_MEMBERS
                or any(not isinstance(v, UUID) for v in self.members + self.courses)
                or list(self.members) != sorted(set(self.members), key=str)
                or list(self.courses) != sorted(set(self.courses), key=str)
                or (self.members and self.scope_type not in ('course', 'semester'))
                or (self.courses and self.scope_type != 'semester')
                or any(not isinstance(v, UUID) for v in (self.intent_id, self.request_id, self.owner_id, self.scope_id))
                or (self.scope_type == 'account' and self.scope_id != self.owner_id)
                or self.intent_id != _derived('intent', self.owner_id, self.scope_type, self.scope_id)
                or self.request_id != _derived('request', self.intent_id)):
            raise ValueError('invalid deletion intent')

    @classmethod
    def for_scope(cls, owner_id: UUID, scope_type: str, scope_id: UUID, origin: str = 'user',
                  members=(), courses=()) -> 'DeletionIntent':
        # One intent per owner and scope: a repeated or concurrent deletion of the same
        # object, from any request key, maps to the same ledger entry and local request.
        intent_id = _derived('intent', owner_id, scope_type, scope_id)
        return cls(intent_id, _derived('request', intent_id), owner_id, scope_type, scope_id, origin,
                   tuple(sorted(set(members), key=str)), tuple(sorted(set(courses), key=str)))

    def identity(self) -> tuple:
        """What makes two records the same intent; the descendant lists are evidence, not identity."""
        return (self.intent_id, self.request_id, self.owner_id, self.scope_type, self.scope_id, self.origin)

    def record(self) -> dict:
        return dict(intent_id=str(self.intent_id), request_id=str(self.request_id), owner_id=str(self.owner_id),
                    scope_type=self.scope_type, scope_id=str(self.scope_id), origin=self.origin,
                    members=[str(member) for member in self.members],
                    courses=[str(course) for course in self.courses])

    @classmethod
    def from_record(cls, value: dict) -> 'DeletionIntent':
        if (not isinstance(value, dict)
                or set(value) != {'intent_id', 'request_id', 'owner_id', 'scope_type', 'scope_id', 'origin',
                                  'members', 'courses'}
                or any(not isinstance(value[name], list) or any(not isinstance(item, str) for item in value[name])
                       for name in ('members', 'courses'))):
            raise ValueError('invalid deletion intent record')
        return cls(UUID(value['intent_id']), UUID(value['request_id']), UUID(value['owner_id']),
                   value['scope_type'], UUID(value['scope_id']), value['origin'],
                   tuple(UUID(item) for item in value['members']), tuple(UUID(item) for item in value['courses']))


@dataclass(frozen=True)
class LedgerEntry:
    generation: int
    intent: DeletionIntent
    entry_hash: str


class DeletionLedger(Protocol):
    """Append-only journal in a fault domain separate from the database and media."""
    def append(self, intent: DeletionIntent, *, deadline: float | None = None) -> LedgerEntry: ...
    def entries(self) -> list[LedgerEntry]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def deletion_dto(row) -> dict:
    """Database request/execution row to the frozen Deletion DTO."""
    def stamp(value):
        return value.isoformat() if value else None
    return dict(id=str(row['id']), revision=row['revision'], created_at=stamp(row['created_at']),
                updated_at=stamp(row['updated_at']), scope_type=row['scope_type'], scope_id=str(row['scope_id']),
                state=DTO_STATES[row['state']], access_revoked_at=stamp(row['access_revoked_at']),
                physical_completed_at=stamp(row['completed_at']), failure_code=row['last_error_code'])
