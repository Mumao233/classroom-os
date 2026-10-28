"""Data foundation types; these are internal records, not HTTP DTOs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from uuid import UUID


@dataclass(frozen=True)
class OwnerContext:
    """Construct only from server-authenticated identity, never request owner fields."""

    owner_id: UUID

    def __post_init__(self):
        if not isinstance(self.owner_id, UUID) or self.owner_id.version != 4:
            raise ValueError("authenticated owner must be a UUIDv4")


class Resource(str, Enum):
    SEMESTER = "semester"
    COURSE = "course"
    CLASS_SESSION = "class_session"


class NotFound(Exception):
    """Missing or foreign resource; never reveals foreign ownership."""


class Gone(Exception):
    """Owned resource or its current ancestor has been revoked."""


class InactiveOwner(Exception):
    """Authentication context has no active principal."""


class RevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("resource revision changed")


@dataclass(frozen=True, kw_only=True)
class MutableRecord:
    id: UUID
    owner_id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    archived_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class Semester(MutableRecord):
    name: str
    academic_year: str | None
    start_date: date | None
    end_date: date | None


@dataclass(frozen=True, kw_only=True)
class Course(MutableRecord):
    semester_id: UUID
    name: str
    english_name: str | None
    language: str
    teacher: str | None
    room: str | None
    color_token: str
    icon_key: str
    current_material_version_id: UUID | None


@dataclass(frozen=True, kw_only=True)
class MaterialVersion:
    id: UUID
    owner_id: UUID
    created_at: datetime
    course_id: UUID
    revision: int
    syllabus: str
    glossary: list[dict[str, str]]
    language: str
    content_sha256: str


@dataclass(frozen=True, kw_only=True)
class ClassSession(MutableRecord):
    course_id: UUID
    title: str
    scheduled_class_ref: UUID | None
    course_material_version_id: UUID
    capture_status: str
    started_at: datetime | None
    ended_at: datetime | None
    all_expected_uploaded_at: datetime | None
    audio_expires_at: datetime | None
    adopted_transcript_version_id: UUID | None
    legacy_origin: dict | None
    automatic_explanation_policy: dict


@dataclass(frozen=True, kw_only=True)
class Tombstone:
    id: UUID
    owner_id: UUID
    created_at: datetime
    request_id: UUID
    resource_type: str
    resource_id: UUID
    deleted_revision: int | None
    erasure_epoch: int
    ledger_sequence: int
