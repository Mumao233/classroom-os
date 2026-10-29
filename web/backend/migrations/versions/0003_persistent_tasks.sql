-- Frozen additive PR-02b schema and scheduling boundary. Apply only through Alembic.


CREATE TABLE operation_receipts (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	owner_id UUID NOT NULL,
	operation TEXT NOT NULL,
	op_id UUID NOT NULL,
	request_hash CHAR(64) NOT NULL,
	resource_type TEXT NOT NULL,
	resource_id UUID,
	response_status INTEGER NOT NULL,
	response_body JSONB NOT NULL,
	schema_version TEXT DEFAULT '1' NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_operation_receipts PRIMARY KEY (id),
	CONSTRAINT uq_operation_receipts_owner_id UNIQUE (owner_id, id),
	CONSTRAINT fk_operation_receipts_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_receipt_operation UNIQUE (owner_id, operation, op_id),
	CONSTRAINT ck_operation_receipts_hash CHECK (request_hash ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_operation_receipts_operation CHECK (char_length(operation) BETWEEN 1 AND 100),
	CONSTRAINT ck_operation_receipts_response_status CHECK (response_status BETWEEN 100 AND 599),
	CONSTRAINT ck_operation_receipts_resource_type CHECK (resource_type IN ('task','semester','course','class_session','course_material_version')),
	CONSTRAINT ck_operation_receipts_bounded_response CHECK (jsonb_typeof(response_body) = 'object' AND pg_column_size(response_body) <= 4096),
	CONSTRAINT ck_operation_receipts_schema_version CHECK (schema_version = '1')
)

;


CREATE TABLE tasks (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	owner_id UUID NOT NULL,
	revision BIGINT DEFAULT '1' NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	class_session_id UUID,
	parent_task_id UUID,
	kind TEXT NOT NULL,
	state TEXT NOT NULL,
	input_ref JSONB NOT NULL,
	schema_version TEXT DEFAULT '1' NOT NULL,
	priority INTEGER DEFAULT '0' NOT NULL,
	not_before TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	lease_token BIGINT DEFAULT '0' NOT NULL,
	may_have_charged BOOLEAN DEFAULT false NOT NULL,
	leased_by TEXT,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	error_code TEXT,
	cancel_requested_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	plan_id UUID,
	CONSTRAINT pk_tasks PRIMARY KEY (id),
	CONSTRAINT uq_tasks_owner_id UNIQUE (owner_id, id),
	CONSTRAINT fk_tasks_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_task_plan UNIQUE (owner_id, plan_id),
	CONSTRAINT fk_tasks_class_session_id FOREIGN KEY(owner_id, class_session_id) REFERENCES class_sessions (owner_id, id) MATCH SIMPLE ON DELETE RESTRICT NOT DEFERRABLE,
	CONSTRAINT fk_tasks_parent_task_id FOREIGN KEY(owner_id, parent_task_id) REFERENCES tasks (owner_id, id) MATCH SIMPLE ON DELETE RESTRICT NOT DEFERRABLE,
	CONSTRAINT ck_tasks_state CHECK (state IN ('queued','running','succeeded','failed','unknown','cancel_requested','cancelled')),
	CONSTRAINT ck_tasks_kind CHECK (kind IN ('asr_live','asr_retranscribe','ai_question','ai_explanation','review_summary','export','import','delete')),
	CONSTRAINT ck_tasks_counters CHECK (revision >= 1 AND lease_token >= 0),
	CONSTRAINT ck_tasks_plan_not_implemented CHECK (plan_id IS NULL),
	CONSTRAINT ck_tasks_bounded_input CHECK (jsonb_typeof(input_ref) = 'object' AND pg_column_size(input_ref) <= 16384),
	CONSTRAINT ck_tasks_schema_version CHECK (schema_version = '1'),
	CONSTRAINT ck_tasks_worker CHECK (leased_by IS NULL OR char_length(leased_by) BETWEEN 1 AND 100),
	CONSTRAINT ck_tasks_error_code CHECK (error_code IS NULL OR char_length(error_code) <= 64),
	CONSTRAINT ck_tasks_lease_pair CHECK ((leased_by IS NULL) = (lease_expires_at IS NULL))
)

;

CREATE INDEX ix_tasks_claim ON tasks (state, not_before, priority);

CREATE INDEX ix_tasks_owner_created ON tasks (owner_id, created_at, id);

CREATE INDEX ix_tasks_recovery ON tasks (state, lease_expires_at);


CREATE TABLE task_attempts (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	owner_id UUID NOT NULL,
	task_id UUID NOT NULL,
	attempt_number INTEGER NOT NULL,
	state TEXT NOT NULL,
	provider TEXT,
	provider_request_id TEXT,
	model TEXT,
	price_revision TEXT,
	submitted_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	raw_output_ref UUID,
	provider_outcome TEXT,
	error_code TEXT,
	duplicate_billing_ack BOOLEAN DEFAULT false NOT NULL,
	CONSTRAINT pk_task_attempts PRIMARY KEY (id),
	CONSTRAINT uq_task_attempts_owner_id UNIQUE (owner_id, id),
	CONSTRAINT fk_task_attempts_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_attempt_number UNIQUE (owner_id, task_id, attempt_number),
	CONSTRAINT fk_task_attempts_task_id FOREIGN KEY(owner_id, task_id) REFERENCES tasks (owner_id, id) MATCH SIMPLE ON DELETE RESTRICT NOT DEFERRABLE,
	CONSTRAINT ck_task_attempts_attempt_number CHECK (attempt_number >= 1),
	CONSTRAINT ck_task_attempts_state CHECK (state IN ('planned','submitting','accepted','succeeded','failed','unknown','cancel_requested','cancelled')),
	CONSTRAINT ck_task_attempts_provider CHECK (provider IS NULL OR char_length(provider) BETWEEN 1 AND 100),
	CONSTRAINT ck_task_attempts_request_id CHECK (provider_request_id IS NULL OR char_length(provider_request_id) <= 200),
	CONSTRAINT ck_task_attempts_model CHECK (model IS NULL OR char_length(model) <= 200),
	CONSTRAINT ck_task_attempts_price_revision CHECK (price_revision IS NULL OR char_length(price_revision) <= 100),
	CONSTRAINT ck_task_attempts_provider_outcome CHECK (provider_outcome IS NULL OR provider_outcome IN ('not_submitted','succeeded','failed','unknown')),
	CONSTRAINT ck_task_attempts_error_code CHECK (error_code IS NULL OR char_length(error_code) <= 64)
)

;

CREATE INDEX ix_attempts_owner_task ON task_attempts (owner_id, task_id);


CREATE TABLE provider_results (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	owner_id UUID NOT NULL,
	attempt_id UUID NOT NULL,
	raw_output TEXT,
	asset_id UUID,
	sha256 CHAR(64) NOT NULL,
	validation_status TEXT NOT NULL,
	validated_payload JSONB,
	conflict_sha256 CHAR(64),
	conflict_detected_at TIMESTAMP WITH TIME ZONE,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_provider_results PRIMARY KEY (id),
	CONSTRAINT uq_provider_results_owner_id UNIQUE (owner_id, id),
	CONSTRAINT fk_provider_results_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_result_attempt UNIQUE (owner_id, attempt_id),
	CONSTRAINT uq_result_exact_attempt UNIQUE (owner_id, id, attempt_id),
	CONSTRAINT fk_provider_results_attempt_id FOREIGN KEY(owner_id, attempt_id) REFERENCES task_attempts (owner_id, id) MATCH SIMPLE ON DELETE RESTRICT NOT DEFERRABLE,
	CONSTRAINT ck_provider_results_raw_source CHECK (num_nonnulls(raw_output,asset_id) = 1),
	CONSTRAINT ck_provider_results_asset_not_implemented CHECK (asset_id IS NULL),
	CONSTRAINT ck_provider_results_bounded_raw CHECK (raw_output IS NULL OR char_length(raw_output) <= 200000),
	CONSTRAINT ck_provider_results_hash CHECK (sha256 ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_provider_results_conflict_hash CHECK (conflict_sha256 IS NULL OR (conflict_sha256 ~ '^[0-9a-f]{64}$' AND conflict_sha256 <> sha256)),
	CONSTRAINT ck_provider_results_conflict_pair CHECK ((conflict_sha256 IS NULL) = (conflict_detected_at IS NULL)),
	CONSTRAINT ck_provider_results_validation_status CHECK (validation_status IN ('pending','valid','invalid')),
	CONSTRAINT ck_provider_results_bounded_validated CHECK (validated_payload IS NULL OR (jsonb_typeof(validated_payload) = 'object' AND pg_column_size(validated_payload) <= 1048576))
)

;

CREATE INDEX ix_results_owner_created ON provider_results (owner_id, created_at);


CREATE TABLE owner_event_counters (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	owner_id UUID NOT NULL,
	next_sequence BIGINT DEFAULT '1' NOT NULL,
	CONSTRAINT pk_owner_event_counters PRIMARY KEY (id),
	CONSTRAINT uq_owner_event_counters_owner_id UNIQUE (owner_id, id),
	CONSTRAINT fk_owner_event_counters_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_event_counter_owner UNIQUE (owner_id),
	CONSTRAINT ck_owner_event_counters_next_sequence CHECK (next_sequence >= 1)
)

;


CREATE TABLE outbox_events (
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	owner_id UUID NOT NULL,
	event_sequence BIGINT NOT NULL,
	event_type TEXT NOT NULL,
	aggregate_type TEXT NOT NULL,
	aggregate_id UUID NOT NULL,
	aggregate_revision BIGINT,
	payload JSONB NOT NULL,
	schema_version TEXT DEFAULT '1' NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_outbox_events PRIMARY KEY (id),
	CONSTRAINT uq_outbox_events_owner_id UNIQUE (owner_id, id),
	CONSTRAINT fk_outbox_events_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
	CONSTRAINT uq_owner_event_sequence UNIQUE (owner_id, event_sequence),
	CONSTRAINT ck_outbox_events_sequence CHECK (event_sequence >= 1),
	CONSTRAINT ck_outbox_events_revision CHECK (aggregate_revision IS NULL OR aggregate_revision >= 1),
	CONSTRAINT ck_outbox_events_event_type CHECK (char_length(event_type) BETWEEN 1 AND 100 AND char_length(aggregate_type) BETWEEN 1 AND 100),
	CONSTRAINT ck_outbox_events_bounded_payload CHECK (jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 8192),
	CONSTRAINT ck_outbox_events_schema_version CHECK (schema_version = '1')
)

;

CREATE INDEX ix_outbox_owner_sequence ON outbox_events (owner_id, event_sequence);

CREATE INDEX ix_outbox_publication ON outbox_events (published_at, created_at);

ALTER TABLE task_attempts ADD CONSTRAINT fk_task_attempts_raw_output_ref FOREIGN KEY(owner_id, raw_output_ref) REFERENCES provider_results (owner_id, id) MATCH SIMPLE ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE task_attempts ADD CONSTRAINT fk_attempt_result_exact_lineage FOREIGN KEY(owner_id, raw_output_ref, id) REFERENCES provider_results (owner_id, id, attempt_id) MATCH SIMPLE ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;

-- Scheduling identity cannot log in, own tables or bypass RLS. Its fixed
-- functions can only inspect queue metadata and create minimal state events.
DO $roles$
DECLARE candidate text;
BEGIN
  FOREACH candidate IN ARRAY ARRAY['classroom_web_scheduler','classroom_web_task_dispatcher'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = candidate) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', candidate);
    ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname = candidate AND
        (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin OR rolreplication)) THEN
      RAISE EXCEPTION 'scheduling group must be unprivileged NOLOGIN';
    END IF;
    IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = candidate) THEN
      RAISE EXCEPTION 'scheduling groups must not inherit other roles';
    END IF;
  END LOOP;
END
$roles$;
GRANT USAGE ON SCHEMA public TO classroom_web_scheduler, classroom_web_task_dispatcher;
REVOKE CREATE ON SCHEMA public FROM classroom_web_scheduler, classroom_web_task_dispatcher;

DO $policies$
DECLARE target text;
BEGIN
  FOREACH target IN ARRAY ARRAY['operation_receipts','tasks','task_attempts','provider_results','owner_event_counters','outbox_events'] LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, classroom_web_scheduler, classroom_web_task_dispatcher', target);
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', target);
    EXECUTE format($policy$CREATE POLICY owner_isolation ON public.%I TO classroom_web_runtime
      USING (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid AND public.classroom_web_owner_active())
      WITH CHECK (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid AND public.classroom_web_owner_active())$policy$, target);
    EXECUTE format('GRANT SELECT, INSERT ON public.%I TO classroom_web_runtime', target);
  END LOOP;
END
$policies$;
GRANT UPDATE ON tasks, task_attempts, provider_results, owner_event_counters TO classroom_web_runtime;
GRANT UPDATE (published_at) ON outbox_events TO classroom_web_runtime;

CREATE POLICY dispatch_metadata ON tasks TO classroom_web_task_dispatcher USING (true) WITH CHECK (true);
CREATE POLICY dispatch_counter ON owner_event_counters TO classroom_web_task_dispatcher USING (true) WITH CHECK (true);
CREATE POLICY dispatch_event ON outbox_events TO classroom_web_task_dispatcher WITH CHECK (true);
GRANT SELECT (id, owner_id, kind, state, not_before, priority, created_at,
              lease_token, leased_by, lease_expires_at, revision, class_session_id, error_code, may_have_charged)
  ON tasks TO classroom_web_task_dispatcher;
GRANT UPDATE (state, lease_token, leased_by, lease_expires_at, revision, updated_at, error_code)
  ON tasks TO classroom_web_task_dispatcher;
GRANT SELECT (owner_id, next_sequence) ON owner_event_counters TO classroom_web_task_dispatcher;
GRANT INSERT (id, owner_id, next_sequence) ON owner_event_counters TO classroom_web_task_dispatcher;
GRANT UPDATE (next_sequence) ON owner_event_counters TO classroom_web_task_dispatcher;
GRANT INSERT (id, owner_id, event_sequence, event_type, aggregate_type,
              aggregate_id, aggregate_revision, payload, schema_version)
  ON outbox_events TO classroom_web_task_dispatcher;

CREATE FUNCTION classroom_web_guard_execution() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $guard$
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- The deferrable triple FK enforces exact attempt/result lineage even for
    -- an INSERT that starts with a nonnull raw link. It also covers direct SQL.
    RETURN NEW;
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
      OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'execution identity is immutable' USING ERRCODE = '23514';
  END IF;
  IF TG_TABLE_NAME = 'tasks' THEN
    IF NEW.kind IS DISTINCT FROM OLD.kind OR NEW.class_session_id IS DISTINCT FROM OLD.class_session_id
        OR NEW.parent_task_id IS DISTINCT FROM OLD.parent_task_id OR NEW.input_ref IS DISTINCT FROM OLD.input_ref
        OR NEW.plan_id IS DISTINCT FROM OLD.plan_id OR NEW.schema_version IS DISTINCT FROM OLD.schema_version THEN
      RAISE EXCEPTION 'task input and intent are immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.lease_token < OLD.lease_token OR NEW.revision < OLD.revision THEN
      RAISE EXCEPTION 'task fence and revision cannot decrease' USING ERRCODE = '23514';
    END IF;
    IF OLD.may_have_charged AND NOT NEW.may_have_charged THEN
      RAISE EXCEPTION 'possible charge evidence cannot be cleared' USING ERRCODE = '23514';
    END IF;
    IF OLD.state = 'unknown' AND NEW.state IN ('queued','running') THEN
      RAISE EXCEPTION 'unknown task cannot be automatically resubmitted' USING ERRCODE = '23514';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NEW.revision <> OLD.revision + 1 THEN
      RAISE EXCEPTION 'state mutation requires next revision' USING ERRCODE = '23514';
    END IF;
  ELSIF TG_TABLE_NAME = 'task_attempts' THEN
    IF NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
        OR NEW.duplicate_billing_ack IS DISTINCT FROM OLD.duplicate_billing_ack THEN
      RAISE EXCEPTION 'attempt identity and acknowledgement are immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.submitted_at IS NOT NULL AND (NEW.submitted_at IS DISTINCT FROM OLD.submitted_at OR NEW.state = 'planned') THEN
      RAISE EXCEPTION 'committed dispatch intent cannot be removed' USING ERRCODE = '23514';
    END IF;
    IF OLD.raw_output_ref IS NOT NULL AND NEW.raw_output_ref IS DISTINCT FROM OLD.raw_output_ref THEN
      RAISE EXCEPTION 'attempt raw result reference is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.raw_output_ref IS DISTINCT FROM OLD.raw_output_ref AND NEW.raw_output_ref IS NOT NULL THEN
      -- Separate PL/pgSQL statements keep the metadata-only revocation role
      -- from even preparing a provider_results query when the link is unchanged.
      IF NOT EXISTS (SELECT FROM public.provider_results r WHERE r.owner_id = NEW.owner_id
          AND r.id = NEW.raw_output_ref AND r.attempt_id = NEW.id) THEN
        RAISE EXCEPTION 'raw result must belong to this exact attempt' USING ERRCODE = '23514';
      END IF;
    END IF;
    IF OLD.state = 'unknown' AND NEW.state IN ('planned','submitting','accepted') THEN
      RAISE EXCEPTION 'unknown attempt cannot be resubmitted' USING ERRCODE = '23514';
    END IF;
  ELSIF TG_TABLE_NAME = 'provider_results' THEN
    IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id OR NEW.raw_output IS DISTINCT FROM OLD.raw_output
        OR NEW.asset_id IS DISTINCT FROM OLD.asset_id OR NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.received_at IS DISTINCT FROM OLD.received_at THEN
      RAISE EXCEPTION 'provider raw result is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.conflict_sha256 IS NOT NULL AND (NEW.conflict_sha256 IS DISTINCT FROM OLD.conflict_sha256
        OR NEW.conflict_detected_at IS DISTINCT FROM OLD.conflict_detected_at) THEN
      RAISE EXCEPTION 'first conflicting result diagnostic is immutable' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$guard$;
REVOKE ALL ON FUNCTION classroom_web_guard_execution() FROM PUBLIC;
CREATE TRIGGER guard_task_execution BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_execution();
CREATE TRIGGER guard_attempt_execution BEFORE INSERT OR UPDATE ON task_attempts FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_execution();
CREATE TRIGGER guard_result_execution BEFORE UPDATE ON provider_results FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_execution();

CREATE FUNCTION classroom_web_claim_tasks(worker text, lease_seconds integer, batch_size integer DEFAULT 1)
RETURNS TABLE(id uuid, owner_id uuid, kind text, lease_token bigint, lease_expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $claim$
DECLARE candidate record;
        fence bigint;
        expiry timestamptz;
        sequence bigint;
BEGIN
  IF worker IS NULL OR char_length(worker) NOT BETWEEN 1 AND 100
      OR lease_seconds IS NULL OR lease_seconds NOT BETWEEN 1 AND 3600
      OR batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid claim limits' USING ERRCODE = '22023';
  END IF;
  FOR candidate IN
    -- Materialize and lock the whole batch before locking any owner counter.
    -- Owners are then processed in one order across workers, so two batches
    -- cannot acquire A/B event counters in opposite orders and deadlock.
    WITH picked AS MATERIALIZED (
      SELECT t.id, t.owner_id, t.kind, t.lease_token, t.revision, t.class_session_id, t.may_have_charged
      FROM public.tasks t
      WHERE t.state = 'queued' AND t.not_before <= clock_timestamp()
      -- Local scheduling policy: tasks overdue ten minutes are served oldest
      -- first; younger tasks retain explicit priority. This prevents permanent
      -- starvation without altering provider/fee semantics.
      ORDER BY (t.not_before <= statement_timestamp() - interval '10 minutes') DESC,
        CASE WHEN t.not_before <= statement_timestamp() - interval '10 minutes' THEN t.not_before END,
        t.priority DESC, t.not_before, t.created_at, t.id
      LIMIT batch_size FOR UPDATE SKIP LOCKED
    ) SELECT * FROM picked ORDER BY picked.owner_id, picked.id
  LOOP
    fence := candidate.lease_token + 1;
    expiry := clock_timestamp() + make_interval(secs => lease_seconds);
    UPDATE public.tasks t SET state = 'running', leased_by = worker,
      lease_token = fence, lease_expires_at = expiry,
      revision = candidate.revision + 1, updated_at = clock_timestamp(), error_code = NULL
    WHERE t.id = candidate.id AND t.owner_id = candidate.owner_id;
    INSERT INTO public.owner_event_counters(id, owner_id, next_sequence)
      VALUES(gen_random_uuid(), candidate.owner_id, 1) ON CONFLICT DO NOTHING;
    UPDATE public.owner_event_counters c SET next_sequence = c.next_sequence + 1
      WHERE c.owner_id = candidate.owner_id RETURNING c.next_sequence - 1 INTO sequence;
    INSERT INTO public.outbox_events(id, owner_id, event_sequence, event_type,
      aggregate_type, aggregate_id, aggregate_revision, payload, schema_version)
    VALUES(gen_random_uuid(), candidate.owner_id, sequence, 'task.changed', 'task', candidate.id,
      candidate.revision + 1, jsonb_build_object('task_id', candidate.id,
        'class_session_id', candidate.class_session_id, 'revision', candidate.revision + 1,
        'state', 'running', 'error_code', NULL, 'may_have_charged', candidate.may_have_charged), '1');
    id := candidate.id; owner_id := candidate.owner_id; kind := candidate.kind;
    lease_token := fence; lease_expires_at := expiry;
    RETURN NEXT;
  END LOOP;
END
$claim$;
REVOKE ALL ON FUNCTION classroom_web_claim_tasks(text, integer, integer) FROM PUBLIC, classroom_web_runtime;
ALTER FUNCTION classroom_web_claim_tasks(text, integer, integer) OWNER TO classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_claim_tasks(text, integer, integer) TO classroom_web_scheduler;

CREATE FUNCTION classroom_web_expired_tasks(batch_size integer DEFAULT 100)
RETURNS TABLE(id uuid, owner_id uuid, kind text, lease_token bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $expired$
BEGIN
  IF batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid recovery batch limit' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY SELECT t.id, t.owner_id, t.kind, t.lease_token FROM public.tasks t
    WHERE t.state IN ('running','cancel_requested') AND t.lease_expires_at <= statement_timestamp()
    ORDER BY t.lease_expires_at, t.id LIMIT batch_size;
END
$expired$;
REVOKE ALL ON FUNCTION classroom_web_expired_tasks(integer) FROM PUBLIC, classroom_web_runtime;
ALTER FUNCTION classroom_web_expired_tasks(integer) OWNER TO classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_expired_tasks(integer) TO classroom_web_scheduler;

-- Revocation fallback can terminate expired work while the regular owner UoW
-- is correctly denied. These grants contain only opaque IDs/access metadata;
-- no task input, source text, raw output, user email, settings or key material.
GRANT SELECT (id, state, deleted_at) ON users TO classroom_web_task_dispatcher;
GRANT SELECT (id, owner_id, deleted_at) ON semesters TO classroom_web_task_dispatcher;
GRANT SELECT (id, owner_id, semester_id, deleted_at) ON courses TO classroom_web_task_dispatcher;
GRANT SELECT (id, owner_id, course_id, course_material_version_id, deleted_at)
  ON class_sessions TO classroom_web_task_dispatcher;
GRANT SELECT (id, owner_id, course_id) ON course_material_versions TO classroom_web_task_dispatcher;
GRANT SELECT (owner_id, resource_type, resource_id) ON deletion_tombstones TO classroom_web_task_dispatcher;
CREATE POLICY dispatch_access_metadata ON semesters FOR SELECT TO classroom_web_task_dispatcher USING (true);
CREATE POLICY dispatch_access_metadata ON courses FOR SELECT TO classroom_web_task_dispatcher USING (true);
CREATE POLICY dispatch_access_metadata ON class_sessions FOR SELECT TO classroom_web_task_dispatcher USING (true);
CREATE POLICY dispatch_access_metadata ON course_material_versions FOR SELECT TO classroom_web_task_dispatcher USING (true);
CREATE POLICY dispatch_access_metadata ON deletion_tombstones FOR SELECT TO classroom_web_task_dispatcher USING (true);
GRANT SELECT (id, owner_id, task_id, state, submitted_at, raw_output_ref, provider_outcome, finished_at)
  ON task_attempts TO classroom_web_task_dispatcher;
GRANT UPDATE (state, error_code, provider_outcome, finished_at)
  ON task_attempts TO classroom_web_task_dispatcher;
CREATE POLICY dispatch_attempt_metadata ON task_attempts TO classroom_web_task_dispatcher
  USING (true) WITH CHECK (true);
GRANT UPDATE (may_have_charged, completed_at) ON tasks TO classroom_web_task_dispatcher;

CREATE FUNCTION classroom_web_recover_revoked(task uuid, token bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $revoked$
DECLARE candidate record;
        inaccessible boolean;
        charged boolean;
        new_state text;
        failure text;
        sequence bigint;
BEGIN
  IF task IS NULL OR token IS NULL OR token < 0 THEN
    RAISE EXCEPTION 'invalid revoked recovery identity' USING ERRCODE = '22023';
  END IF;
  SELECT t.id, t.owner_id, t.kind, t.state, t.class_session_id,
      t.lease_token, t.lease_expires_at, t.revision, t.may_have_charged
    INTO candidate FROM public.tasks t WHERE t.id = task FOR UPDATE;
  IF NOT FOUND OR candidate.lease_token <> token
      OR candidate.state NOT IN ('running','cancel_requested')
      OR candidate.lease_expires_at IS NULL OR candidate.lease_expires_at > clock_timestamp() THEN
    RETURN false;
  END IF;
  -- Only monotonically safe termination follows this access-state read. No
  -- ancestor locks are acquired after the task lock, so ordinary owner writes
  -- (ancestor -> task -> counter) cannot deadlock with this path.
  SELECT NOT EXISTS (SELECT FROM public.users u WHERE u.id = candidate.owner_id
      AND u.state = 'active' AND u.deleted_at IS NULL)
    OR EXISTS (SELECT FROM public.deletion_tombstones d
      WHERE d.owner_id = candidate.owner_id AND d.resource_type = 'account'
        AND d.resource_id = candidate.owner_id) INTO inaccessible;
  IF NOT inaccessible AND candidate.class_session_id IS NOT NULL THEN
    SELECT NOT EXISTS (
      SELECT FROM public.class_sessions cs
        JOIN public.courses c ON c.owner_id = cs.owner_id AND c.id = cs.course_id
        JOIN public.semesters s ON s.owner_id = c.owner_id AND s.id = c.semester_id
        JOIN public.course_material_versions m ON m.owner_id = cs.owner_id AND m.id = cs.course_material_version_id
        JOIN public.courses mc ON mc.owner_id = m.owner_id AND mc.id = m.course_id
        JOIN public.semesters ms ON ms.owner_id = mc.owner_id AND ms.id = mc.semester_id
      WHERE cs.owner_id = candidate.owner_id AND cs.id = candidate.class_session_id
        AND cs.deleted_at IS NULL AND c.deleted_at IS NULL AND s.deleted_at IS NULL
        AND mc.deleted_at IS NULL AND ms.deleted_at IS NULL
        AND NOT EXISTS (SELECT FROM public.deletion_tombstones d
          WHERE d.owner_id = candidate.owner_id AND (
            (d.resource_type = 'class_session' AND d.resource_id = cs.id)
            OR (d.resource_type = 'course' AND d.resource_id IN (c.id, mc.id))
            OR (d.resource_type = 'semester' AND d.resource_id IN (s.id, ms.id))
            OR (d.resource_type = 'course_material_version' AND d.resource_id = m.id)))
    ) INTO inaccessible;
  END IF;
  IF NOT inaccessible THEN
    RETURN false;
  END IF;
  PERFORM a.id FROM public.task_attempts a
    WHERE a.owner_id = candidate.owner_id AND a.task_id = candidate.id FOR UPDATE;
  SELECT candidate.may_have_charged OR EXISTS (
    SELECT FROM public.task_attempts a WHERE a.owner_id = candidate.owner_id
      AND a.task_id = candidate.id AND a.submitted_at IS NOT NULL) INTO charged;
  new_state := CASE WHEN charged THEN 'unknown' ELSE 'cancelled' END;
  failure := CASE WHEN charged THEN 'PROVIDER_RESULT_UNKNOWN' ELSE 'ACCESS_REVOKED' END;
  UPDATE public.task_attempts a SET
      state = CASE WHEN a.submitted_at IS NOT NULL THEN 'unknown' ELSE 'cancelled' END,
      provider_outcome = CASE WHEN a.raw_output_ref IS NOT NULL THEN a.provider_outcome
        WHEN a.submitted_at IS NOT NULL THEN 'unknown' ELSE 'not_submitted' END,
      error_code = failure,
      finished_at = CASE WHEN a.submitted_at IS NULL THEN clock_timestamp() ELSE a.finished_at END
    WHERE a.owner_id = candidate.owner_id AND a.task_id = candidate.id;
  UPDATE public.tasks t SET state = new_state, error_code = failure,
      may_have_charged = charged, lease_token = t.lease_token + 1,
      leased_by = NULL, lease_expires_at = NULL, revision = t.revision + 1,
      updated_at = clock_timestamp(), completed_at = CASE WHEN charged THEN NULL ELSE clock_timestamp() END
    WHERE t.id = candidate.id AND t.owner_id = candidate.owner_id;
  INSERT INTO public.owner_event_counters(id, owner_id, next_sequence)
    VALUES(gen_random_uuid(), candidate.owner_id, 1) ON CONFLICT DO NOTHING;
  UPDATE public.owner_event_counters c SET next_sequence = c.next_sequence + 1
    WHERE c.owner_id = candidate.owner_id RETURNING c.next_sequence - 1 INTO sequence;
  INSERT INTO public.outbox_events(id, owner_id, event_sequence, event_type,
      aggregate_type, aggregate_id, aggregate_revision, payload, schema_version)
    VALUES(gen_random_uuid(), candidate.owner_id, sequence, 'task.changed', 'task', candidate.id,
      candidate.revision + 1, jsonb_build_object('task_id', candidate.id,
        'class_session_id', candidate.class_session_id, 'revision', candidate.revision + 1,
        'state', new_state, 'error_code', failure, 'may_have_charged', charged), '1');
  RETURN true;
END
$revoked$;
REVOKE ALL ON FUNCTION classroom_web_recover_revoked(uuid, bigint) FROM PUBLIC, classroom_web_runtime;
ALTER FUNCTION classroom_web_recover_revoked(uuid, bigint) OWNER TO classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_recover_revoked(uuid, bigint) TO classroom_web_scheduler;
