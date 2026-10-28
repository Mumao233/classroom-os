"""Owner-scoped adapters. Call only inside Database.transaction()."""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update

from ..domain.data import (ClassSession, Course, Gone, MaterialVersion, NotFound,
                           Resource, RevisionConflict, Semester, Tombstone)
from ..domain.policies import base_revision, bounded_text, language_value, material_payload
from .schema import tables


RESOURCE_TABLES = {
    Resource.SEMESTER: (tables["semesters"], Semester),
    Resource.COURSE: (tables["courses"], Course),
    Resource.CLASS_SESSION: (tables["class_sessions"], ClassSession),
}
_UNSET = object()


class PgCatalogRepository:
    def __init__(self, connection, owner_id: UUID):
        self.connection = connection
        self.owner_id = owner_id

    def _query(self, table, resource_id):
        return select(table).where(table.c.owner_id == self.owner_id, table.c.id == resource_id)

    def _tombstone(self, resource, resource_id):
        table = tables["deletion_tombstones"]
        return self.connection.execute(select(table).where(
            table.c.owner_id == self.owner_id, table.c.resource_type == resource.value,
            table.c.resource_id == resource_id)).mappings().one_or_none()

    def _load(self, resource: Resource, resource_id: UUID, *, write=False, share=False):
        """Authorize before exposing data; acquire ancestor locks before target locks."""
        table, record_type = RESOURCE_TABLES[resource]
        if self._tombstone(resource, resource_id):
            raise Gone("resource has been revoked")
        row = self.connection.execute(self._query(table, resource_id)).mappings().one_or_none()
        if row is None:
            raise NotFound("resource not found")
        if row["deleted_at"] is not None:
            raise Gone("resource has been revoked")
        parent_key = None
        if resource == Resource.COURSE:
            parent_key = "semester_id"
            self._load(Resource.SEMESTER, row[parent_key], share=write or share)
        elif resource == Resource.CLASS_SESSION:
            parent_key = "course_id"
            self._load(Resource.COURSE, row[parent_key], share=write or share)
        if write or share:
            locked = self.connection.execute(
                self._query(table, resource_id).with_for_update(read=not write)
            ).mappings().one_or_none()
            if locked is None:
                raise NotFound("resource not found")
            if locked["deleted_at"] is not None or self._tombstone(resource, resource_id):
                raise Gone("resource has been revoked")
            if parent_key and locked[parent_key] != row[parent_key]:
                raise RevisionConflict(locked["revision"])
            row = locked
        return record_type(**row)

    def _insert(self, table, record_type, **values):
        row = self.connection.execute(insert(table).values(
            owner_id=self.owner_id, **values).returning(table)).mappings().one()
        return record_type(**row)

    def _cas(self, resource, resource_id, expected, **values):
        base_revision(expected)
        current = self._load(resource, resource_id, write=True)
        if current.revision != expected:
            raise RevisionConflict(current.revision)
        table, record_type = RESOURCE_TABLES[resource]
        row = self.connection.execute(update(table).where(
            table.c.owner_id == self.owner_id, table.c.id == resource_id,
            table.c.revision == expected, table.c.deleted_at.is_(None),
        ).values(**values, revision=table.c.revision + 1,
                 updated_at=func.transaction_timestamp()).returning(table)).mappings().one_or_none()
        if row is None:
            raise RevisionConflict(current.revision)
        return record_type(**row)

    def create_semester(self, name, *, academic_year=None, start_date=None, end_date=None):
        bounded_text(name, 160, nonempty=True)
        if academic_year is not None:
            bounded_text(academic_year, 40)
        if start_date and end_date and start_date > end_date:
            raise ValueError("start date must not follow end date")
        return self._insert(tables["semesters"], Semester, id=uuid4(), name=name,
                            academic_year=academic_year, start_date=start_date, end_date=end_date)

    def get_semester(self, resource_id):
        return self._load(Resource.SEMESTER, resource_id)

    def update_semester(self, resource_id, base_revision, *, name=_UNSET,
                        academic_year=_UNSET, start_date=_UNSET, end_date=_UNSET):
        fields = {}
        if name is not _UNSET:
            fields["name"] = bounded_text(name, 160, nonempty=True)
        if academic_year is not _UNSET:
            fields["academic_year"] = (bounded_text(academic_year, 40)
                                       if academic_year is not None else None)
        if start_date is not _UNSET:
            fields["start_date"] = start_date
        if end_date is not _UNSET:
            fields["end_date"] = end_date
        current = self._load(Resource.SEMESTER, resource_id, write=True)
        start, end = fields.get("start_date", current.start_date), fields.get("end_date", current.end_date)
        if start and end and start > end:
            raise ValueError("start date must not follow end date")
        return self._cas(Resource.SEMESTER, resource_id, base_revision, **fields)

    def create_course(self, semester_id, name, *, language="auto", color_token="blue",
                      icon_key="book", english_name=None, teacher=None, room=None):
        bounded_text(name, 160, nonempty=True)
        language_value(language)
        bounded_text(color_token, 64)
        bounded_text(icon_key, 64)
        for value, limit in ((english_name, 200), (teacher, 100), (room, 100)):
            if value is not None:
                bounded_text(value, limit)
        self._load(Resource.SEMESTER, semester_id, share=True)
        course_id, material_id = uuid4(), uuid4()
        result = self._insert(tables["courses"], Course, id=course_id, semester_id=semester_id,
                              name=name, language=language, color_token=color_token,
                              icon_key=icon_key, english_name=english_name, teacher=teacher,
                              room=room, current_material_version_id=material_id)
        payload, digest = material_payload("", [], language)
        self._insert(tables["course_material_versions"], MaterialVersion, id=material_id,
                     course_id=course_id, revision=1, content_sha256=digest, **payload)
        return result

    def get_course(self, resource_id):
        return self._load(Resource.COURSE, resource_id)

    def rename_course(self, resource_id, base_revision, name):
        return self._cas(Resource.COURSE, resource_id, base_revision,
                         name=bounded_text(name, 160, nonempty=True))

    def move_course(self, resource_id, base_revision, semester_id):
        self._load(Resource.SEMESTER, semester_id, share=True)
        return self._cas(Resource.COURSE, resource_id, base_revision, semester_id=semester_id)

    def get_material(self, resource_id):
        table = tables["course_material_versions"]
        tombstones = tables["deletion_tombstones"]
        if self.connection.scalar(select(tombstones.c.id).where(
            tombstones.c.owner_id == self.owner_id,
            tombstones.c.resource_type == "course_material_version",
            tombstones.c.resource_id == resource_id,
        )):
            raise Gone("material version has been revoked")
        row = self.connection.execute(self._query(table, resource_id)).mappings().one_or_none()
        if row is None:
            raise NotFound("material version not found")
        self._load(Resource.COURSE, row["course_id"])
        return MaterialVersion(**row)

    def update_material(self, course_id, base_revision, *, syllabus, glossary, language):
        """CAS compares material revision, independently from course metadata revision."""
        payload, digest = material_payload(syllabus, glossary, language)
        from ..domain.policies import base_revision as validate_revision
        validate_revision(base_revision)
        course = self._load(Resource.COURSE, course_id, write=True)
        current = self.get_material(course.current_material_version_id)
        if current.revision != base_revision:
            raise RevisionConflict(current.revision)
        material = self._insert(tables["course_material_versions"], MaterialVersion,
                                id=uuid4(), course_id=course_id,
                                revision=current.revision + 1, content_sha256=digest, **payload)
        self._cas(Resource.COURSE, course_id, course.revision,
                  current_material_version_id=material.id, language=language)
        return material

    def create_class_session(self, course_id, title):
        bounded_text(title, 200, nonempty=True)
        course = self._load(Resource.COURSE, course_id, share=True)
        return self._insert(tables["class_sessions"], ClassSession, id=uuid4(),
                            course_id=course_id, title=title,
                            course_material_version_id=course.current_material_version_id,
                            capture_status="not_started",
                            automatic_explanation_policy={"schema_version": 1, "enabled": False})

    def get_class_session(self, resource_id):
        return self._load(Resource.CLASS_SESSION, resource_id)

    def move_class_session(self, resource_id, base_revision, course_id):
        self._load(Resource.COURSE, course_id, share=True)
        return self._cas(Resource.CLASS_SESSION, resource_id, base_revision, course_id=course_id)


class PgTombstoneRepository:
    """Revocation primitive; external journal/cleanup orchestration belongs to PR-04b."""

    def __init__(self, catalog: PgCatalogRepository):
        self.catalog = catalog
        self.connection = catalog.connection

    def get(self, resource, resource_id):
        resource = Resource(resource)
        row = self.catalog._tombstone(resource, resource_id)
        return Tombstone(**row) if row else None

    def revoke(self, resource, resource_id, base_revision, ledger_sequence, erasure_epoch=1):
        resource = Resource(resource)
        from ..domain.policies import base_revision as validate_revision
        validate_revision(base_revision)
        for value in (ledger_sequence, erasure_epoch):
            if type(value) is not int or value < 1:
                raise ValueError("ledger sequence and erasure epoch must be positive integers")
        current = self.catalog._load(resource, resource_id, write=True)
        if current.revision != base_revision:
            raise RevisionConflict(current.revision)
        request_id = uuid4()
        self.connection.execute(insert(tables["deletion_requests"]).values(
            id=request_id, owner_id=self.catalog.owner_id, scope_type=resource.value,
            scope_id=resource_id, state="access_revoked",
            access_revoked_at=func.transaction_timestamp()))
        result = self.catalog._insert(tables["deletion_tombstones"], Tombstone, id=uuid4(),
                                      request_id=request_id, resource_type=resource.value,
                                      resource_id=resource_id, deleted_revision=current.revision + 1,
                                      erasure_epoch=erasure_epoch, ledger_sequence=ledger_sequence)
        # _cas intentionally performs its live check BEFORE tombstone insertion; here
        # the locked row and the explicit SQL predicate finish the same atomic change.
        table, _ = RESOURCE_TABLES[resource]
        updated = self.connection.execute(update(table).where(
            table.c.owner_id == self.catalog.owner_id, table.c.id == resource_id,
            table.c.revision == base_revision, table.c.deleted_at.is_(None),
        ).values(deleted_at=func.transaction_timestamp(), revision=table.c.revision + 1,
                 updated_at=func.transaction_timestamp()).returning(table.c.id)).scalar_one_or_none()
        if updated is None:
            raise RevisionConflict(current.revision)
        return result
