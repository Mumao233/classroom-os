"""PR-02a PostgreSQL metadata; no API DTOs or runtime contract-file loading."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
})


def _base(*, owner=False, mutable=False):
    columns = [sa.Column("id", UUID(as_uuid=True), primary_key=True),
               sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                         server_default=sa.text("transaction_timestamp()"))]
    if owner:
        columns.append(sa.Column("owner_id", UUID(as_uuid=True), nullable=False))
    if mutable:
        columns.extend([
            sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("transaction_timestamp()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("revision >= 1", name="revision_positive"),
        ])
    if owner:
        columns.extend([
            sa.UniqueConstraint("owner_id", "id"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        ])
    return columns


def _parent(table, field, target, *, deferred=False):
    return sa.ForeignKeyConstraint(
        ["owner_id", field], [f"{target}.owner_id", f"{target}.id"],
        name=f"fk_{table}_{field}", ondelete="RESTRICT", match="SIMPLE",
        deferrable=deferred, initially="DEFERRED" if deferred else None,
        use_alter=deferred,
    )


users = sa.Table("users", metadata, *_base(mutable=True),
    sa.Column("normalized_email", sa.Text, unique=True),
    sa.Column("password_hash", sa.Text),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("email_verified_at", sa.DateTime(timezone=True)),
    sa.Column("security_epoch", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("profile", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("preferences", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint("role IN ('student','admin')", name="role"),
    sa.CheckConstraint("state IN ('active','disabled','erasing','erased')", name="state"),
)

semesters = sa.Table("semesters", metadata, *_base(owner=True, mutable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("academic_year", sa.Text),
    sa.Column("start_date", sa.Date),
    sa.Column("end_date", sa.Date),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("start_date IS NULL OR end_date IS NULL OR start_date <= end_date",
                       name="date_order"),
    sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
    sa.CheckConstraint("academic_year IS NULL OR char_length(academic_year) <= 30",
                       name="academic_year_length"),
)

courses = sa.Table("courses", metadata, *_base(owner=True, mutable=True),
    sa.Column("semester_id", UUID(as_uuid=True), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("english_name", sa.Text),
    sa.Column("language", sa.Text, nullable=False),
    sa.Column("teacher", sa.Text),
    sa.Column("room", sa.Text),
    sa.Column("color_token", sa.Text, nullable=False),
    sa.Column("icon_key", sa.Text, nullable=False),
    sa.Column("current_material_version_id", UUID(as_uuid=True)),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    _parent("courses", "semester_id", "semesters"),
    _parent("courses", "current_material_version_id", "course_material_versions", deferred=True),
)

course_material_versions = sa.Table("course_material_versions", metadata, *_base(owner=True),
    sa.Column("course_id", UUID(as_uuid=True), nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=False),
    sa.Column("syllabus", sa.Text, nullable=False),
    sa.Column("glossary", JSONB, nullable=False),
    sa.Column("language", sa.Text, nullable=False),
    sa.Column("content_sha256", sa.CHAR(64), nullable=False),
    sa.UniqueConstraint("owner_id", "course_id", "revision", name="uq_material_revision"),
    _parent("course_material_versions", "course_id", "courses"),
    sa.CheckConstraint("revision >= 1", name="revision_positive"),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="hash"),
)

class_sessions = sa.Table("class_sessions", metadata, *_base(owner=True, mutable=True),
    sa.Column("course_id", UUID(as_uuid=True), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("scheduled_class_ref", UUID(as_uuid=True)),
    sa.Column("course_material_version_id", UUID(as_uuid=True), nullable=False),
    sa.Column("capture_status", sa.Text, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.Column("all_expected_uploaded_at", sa.DateTime(timezone=True)),
    sa.Column("audio_expires_at", sa.DateTime(timezone=True)),
    sa.Column("adopted_transcript_version_id", UUID(as_uuid=True)),
    sa.Column("legacy_origin", JSONB),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    sa.Column("automatic_explanation_policy", JSONB, nullable=False,
              server_default='{"schema_version":1,"enabled":false}'),
    _parent("class_sessions", "course_id", "courses"),
    _parent("class_sessions", "course_material_version_id", "course_material_versions"),
    sa.CheckConstraint("scheduled_class_ref IS NULL", name="no_schedule"),
    # Removed only when the transcript-owning migration supplies its composite FK.
    sa.CheckConstraint("adopted_transcript_version_id IS NULL", name="transcript_not_implemented"),
)

deletion_requests = sa.Table("deletion_requests", metadata, *_base(owner=True),
    sa.Column("scope_type", sa.Text, nullable=False),
    sa.Column("scope_id", UUID(as_uuid=True), nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("access_revoked_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deadline_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("last_error_code", sa.Text),
    sa.CheckConstraint("state IN ('access_revoked','pending','failed','complete')", name="state"),
    sa.CheckConstraint("scope_type IN ('semester','course','class_session','account','course_material_version')",
                       name="scope_type"),
)

deletion_tombstones = sa.Table("deletion_tombstones", metadata, *_base(owner=True),
    sa.Column("request_id", UUID(as_uuid=True), nullable=False),
    sa.Column("resource_type", sa.Text, nullable=False),
    sa.Column("resource_id", UUID(as_uuid=True), nullable=False),
    sa.Column("deleted_revision", sa.BigInteger),
    sa.Column("erasure_epoch", sa.BigInteger, nullable=False),
    sa.Column("ledger_sequence", sa.BigInteger, nullable=False),
    sa.UniqueConstraint("owner_id", "resource_type", "resource_id", name="uq_tombstone_resource"),
    _parent("deletion_tombstones", "request_id", "deletion_requests"),
    sa.CheckConstraint("resource_type IN ('semester','course','class_session','account','course_material_version')",
                       name="resource_type"),
    sa.CheckConstraint("erasure_epoch >= 1 AND ledger_sequence >= 1", name="ledger_positive"),
    sa.CheckConstraint("deleted_revision IS NULL OR deleted_revision >= 1", name="revision_positive"),
)

sa.Index("ix_semesters_owner_created", semesters.c.owner_id, semesters.c.created_at, semesters.c.id)
sa.Index("ix_courses_owner_semester_updated", courses.c.owner_id, courses.c.semester_id, courses.c.updated_at)
sa.Index("ix_classes_owner_course_started", class_sessions.c.owner_id, class_sessions.c.course_id, class_sessions.c.started_at)
sa.Index("ix_deletions_owner_scope", deletion_requests.c.owner_id, deletion_requests.c.scope_type, deletion_requests.c.scope_id)
sa.Index("ix_tombstones_ledger", deletion_tombstones.c.ledger_sequence)

tables = dict(metadata.tables)
materials = course_material_versions
tombstones = deletion_tombstones
