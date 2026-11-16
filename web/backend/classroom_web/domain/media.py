"""Private object storage boundary and bounded media policy."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import BinaryIO, Protocol


class ObjectUnavailable(Exception):
    """An object could not be safely read or written."""


def media_state(status, access_revoked_at):
    state = {'staging':'staging', 'verified':'staging', 'committed':'available',
             'delete_pending':'pending_delete', 'delete_failed':'delete_failed', 'deleted':'deleted'}[status]
    if state == 'available' and access_revoked_at is not None:
        return 'access_revoked'
    return state


@dataclass(frozen=True)
class StoredObject:
    key: str
    etag: str
    version_id: str | None
    byte_length: int
    received_at: datetime


class ObjectStore(Protocol):
    def validate_private(self) -> None: ...
    def put(self, key: str, content: BinaryIO, length: int, sha256: str) -> StoredObject: ...
    def inspect(self, key: str) -> StoredObject | None: ...
    def read(self, obj: StoredObject, first: int, last: int) -> bytes: ...
    # PR-04b: remove every stored version and delete marker of one generated key and
    # return only after the store shows the key absent; raise ObjectUnavailable otherwise.
    def erase(self, key: str) -> int: ...
    # PR-04b: one page of generated keys under 'media/' with their last-modified time.
    def keys(self, after: str | None = None, limit: int = 500) -> list[tuple[str, datetime]]: ...


@dataclass(frozen=True)
class MediaPolicy:
    # Experimental server bounds. Product capacity decisions remain TBD-06.
    max_bytes: int = 16_000_000
    upload_lifetime: timedelta = timedelta(hours=1)
    ticket_lifetime: timedelta = timedelta(seconds=60)
    block_bytes: int = 64_000
    max_pending: int = 32

    def __post_init__(self):
        if (not 1 <= self.max_bytes <= 100_000_000 or not 1024 <= self.block_bytes <= 1_000_000
                or not timedelta(seconds=1) <= self.upload_lifetime <= timedelta(days=1)
                or not timedelta(seconds=1) <= self.ticket_lifetime <= timedelta(minutes=5)
                or not 1 <= self.max_pending <= 100):
            raise ValueError('invalid media policy')


def byte_range(value: str | None, length: int) -> tuple[int, int, bool]:
    """A single RFC 9110 byte range, including suffix and open-ended forms."""
    if value is None:
        return 0, length - 1, False
    import re
    match = re.fullmatch(r'bytes=([0-9]{0,18})-([0-9]{0,18})', value)
    if match is None or not any(match.groups()):
        raise ValueError('invalid byte range')
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix == 0:
            raise ValueError('empty suffix')
        return max(0, length - suffix), length - 1, True
    first = int(left)
    last = min(int(right), length - 1) if right else length - 1
    if first >= length or first > last:
        raise ValueError('unsatisfiable byte range')
    return first, last, True
