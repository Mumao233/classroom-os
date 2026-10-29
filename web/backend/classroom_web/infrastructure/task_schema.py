"""PR-02b execution metadata, separate from the PR-02a catalog table registry.

The adjacent Alembic SQL is a frozen snapshot; it never imports this module.
Plan/media references remain constrained NULL until their owning migrations add
the real owner-composite foreign keys. Task revision/updated_at support the
already frozen Task DTO and task.changed event contract.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .schema import metadata, _base, _parent


operation_receipts = sa.Table("operation_receipts", metadata, *_base(owner=True),
    sa.Column("operation", sa.Text, nullable=False),
    sa.Column("op_id", UUID(as_uuid=True), nullable=False),
    sa.Column("request_hash", sa.CHAR(64), nullable=False),
    sa.Column("resource_type", sa.Text, nullable=False),
    sa.Column("resource_id", UUID(as_uuid=True)),
    sa.Column("response_status", sa.Integer, nullable=False),
    sa.Column("response_body", JSONB, nullable=False),
    sa.Column("schema_version", sa.Text, nullable=False, server_default="1"),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("owner_id", "operation", "op_id", name="uq_receipt_operation"),
    sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="hash"),
    sa.CheckConstraint("char_length(operation) BETWEEN 1 AND 100", name="operation"),
    sa.CheckConstraint("response_status BETWEEN 100 AND 599", name="response_status"),
    sa.CheckConstraint("resource_type IN ('task','semester','course','class_session','course_material_version')",
                       name="resource_type"),
    sa.CheckConstraint("jsonb_typeof(response_body) = 'object' AND pg_column_size(response_body) <= 4096",
                       name="bounded_response"),
    sa.CheckConstraint("schema_version = '1'", name="schema_version"),
)

tasks = sa.Table("tasks", metadata, *_base(owner=True),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.text("transaction_timestamp()")),
    sa.Column("class_session_id", UUID(as_uuid=True)),
    sa.Column("parent_task_id", UUID(as_uuid=True)),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("input_ref", JSONB, nullable=False),
    sa.Column("schema_version", sa.Text, nullable=False, server_default="1"),
    sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
    sa.Column("not_before", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.text("transaction_timestamp()")),
    sa.Column("lease_token", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("may_have_charged", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("leased_by", sa.Text),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("error_code", sa.Text),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("plan_id", UUID(as_uuid=True)),
    sa.UniqueConstraint("owner_id", "plan_id", name="uq_task_plan"),
    _parent("tasks", "class_session_id", "class_sessions"),
    _parent("tasks", "parent_task_id", "tasks"),
    sa.CheckConstraint("state IN ('queued','running','succeeded','failed','unknown','cancel_requested','cancelled')",
                       name="state"),
    sa.CheckConstraint("kind IN ('asr_live','asr_retranscribe','ai_question','ai_explanation','review_summary','export','import','delete')",
                       name="kind"),
    sa.CheckConstraint("revision >= 1 AND lease_token >= 0", name="counters"),
    sa.CheckConstraint("plan_id IS NULL", name="plan_not_implemented"),
    sa.CheckConstraint("jsonb_typeof(input_ref) = 'object' AND pg_column_size(input_ref) <= 16384",
                       name="bounded_input"),
    sa.CheckConstraint("schema_version = '1'", name="schema_version"),
    sa.CheckConstraint("leased_by IS NULL OR char_length(leased_by) BETWEEN 1 AND 100", name="worker"),
    sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 64", name="error_code"),
    sa.CheckConstraint("(leased_by IS NULL) = (lease_expires_at IS NULL)", name="lease_pair"),
)

task_attempts = sa.Table("task_attempts", metadata, *_base(owner=True),
    sa.Column("task_id", UUID(as_uuid=True), nullable=False),
    sa.Column("attempt_number", sa.Integer, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("provider", sa.Text),
    sa.Column("provider_request_id", sa.Text),
    sa.Column("model", sa.Text),
    sa.Column("price_revision", sa.Text),
    sa.Column("submitted_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("raw_output_ref", UUID(as_uuid=True)),
    sa.Column("provider_outcome", sa.Text),
    sa.Column("error_code", sa.Text),
    sa.Column("duplicate_billing_ack", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.UniqueConstraint("owner_id", "task_id", "attempt_number", name="uq_attempt_number"),
    _parent("task_attempts", "task_id", "tasks"),
    _parent("task_attempts", "raw_output_ref", "provider_results", deferred=True),
    sa.ForeignKeyConstraint(
        ["owner_id", "raw_output_ref", "id"],
        ["provider_results.owner_id", "provider_results.id", "provider_results.attempt_id"],
        name="fk_attempt_result_exact_lineage", ondelete="RESTRICT", match="SIMPLE",
        deferrable=True, initially="DEFERRED", use_alter=True),
    sa.CheckConstraint("attempt_number >= 1", name="attempt_number"),
    sa.CheckConstraint("state IN ('planned','submitting','accepted','succeeded','failed','unknown','cancel_requested','cancelled')",
                       name="state"),
    sa.CheckConstraint("provider IS NULL OR char_length(provider) BETWEEN 1 AND 100", name="provider"),
    sa.CheckConstraint("provider_request_id IS NULL OR char_length(provider_request_id) <= 200", name="request_id"),
    sa.CheckConstraint("model IS NULL OR char_length(model) <= 200", name="model"),
    sa.CheckConstraint("price_revision IS NULL OR char_length(price_revision) <= 100", name="price_revision"),
    sa.CheckConstraint("provider_outcome IS NULL OR provider_outcome IN ('not_submitted','succeeded','failed','unknown')",
                       name="provider_outcome"),
    sa.CheckConstraint("error_code IS NULL OR char_length(error_code) <= 64", name="error_code"),
)

provider_results = sa.Table("provider_results", metadata, *_base(owner=True),
    sa.Column("attempt_id", UUID(as_uuid=True), nullable=False),
    sa.Column("raw_output", sa.Text),
    sa.Column("asset_id", UUID(as_uuid=True)),
    sa.Column("sha256", sa.CHAR(64), nullable=False),
    sa.Column("validation_status", sa.Text, nullable=False),
    sa.Column("validated_payload", JSONB),
    sa.Column("conflict_sha256", sa.CHAR(64)),
    sa.Column("conflict_detected_at", sa.DateTime(timezone=True)),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.text("transaction_timestamp()")),
    sa.UniqueConstraint("owner_id", "attempt_id", name="uq_result_attempt"),
    sa.UniqueConstraint("owner_id", "id", "attempt_id", name="uq_result_exact_attempt"),
    _parent("provider_results", "attempt_id", "task_attempts"),
    sa.CheckConstraint("num_nonnulls(raw_output,asset_id) = 1", name="raw_source"),
    sa.CheckConstraint("asset_id IS NULL", name="asset_not_implemented"),
    sa.CheckConstraint("raw_output IS NULL OR char_length(raw_output) <= 200000", name="bounded_raw"),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="hash"),
    sa.CheckConstraint("conflict_sha256 IS NULL OR (conflict_sha256 ~ '^[0-9a-f]{64}$' AND conflict_sha256 <> sha256)",
                       name="conflict_hash"),
    sa.CheckConstraint("(conflict_sha256 IS NULL) = (conflict_detected_at IS NULL)", name="conflict_pair"),
    sa.CheckConstraint("validation_status IN ('pending','valid','invalid')", name="validation_status"),
    sa.CheckConstraint("validated_payload IS NULL OR (jsonb_typeof(validated_payload) = 'object' AND pg_column_size(validated_payload) <= 1048576)",
                       name="bounded_validated"),
)

owner_event_counters = sa.Table("owner_event_counters", metadata, *_base(owner=True),
    sa.Column("next_sequence", sa.BigInteger, nullable=False, server_default="1"),
    sa.UniqueConstraint("owner_id", name="uq_event_counter_owner"),
    sa.CheckConstraint("next_sequence >= 1", name="next_sequence"),
)

outbox_events = sa.Table("outbox_events", metadata, *_base(owner=True),
    sa.Column("event_sequence", sa.BigInteger, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("aggregate_type", sa.Text, nullable=False),
    sa.Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    sa.Column("aggregate_revision", sa.BigInteger),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("schema_version", sa.Text, nullable=False, server_default="1"),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("owner_id", "event_sequence", name="uq_owner_event_sequence"),
    sa.CheckConstraint("event_sequence >= 1", name="sequence"),
    sa.CheckConstraint("aggregate_revision IS NULL OR aggregate_revision >= 1", name="revision"),
    sa.CheckConstraint("char_length(event_type) BETWEEN 1 AND 100 AND char_length(aggregate_type) BETWEEN 1 AND 100",
                       name="event_type"),
    sa.CheckConstraint("jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 8192", name="bounded_payload"),
    sa.CheckConstraint("schema_version = '1'", name="schema_version"),
)

sa.Index("ix_tasks_owner_created", tasks.c.owner_id, tasks.c.created_at, tasks.c.id)
sa.Index("ix_tasks_claim", tasks.c.state, tasks.c.not_before, tasks.c.priority)
sa.Index("ix_tasks_recovery", tasks.c.state, tasks.c.lease_expires_at)
sa.Index("ix_attempts_owner_task", task_attempts.c.owner_id, task_attempts.c.task_id)
sa.Index("ix_results_owner_created", provider_results.c.owner_id, provider_results.c.created_at)
sa.Index("ix_outbox_owner_sequence", outbox_events.c.owner_id, outbox_events.c.event_sequence)
sa.Index("ix_outbox_publication", outbox_events.c.published_at, outbox_events.c.created_at)

tables = {table.name: table for table in (operation_receipts, tasks, task_attempts,
          provider_results, owner_event_counters, outbox_events)}
