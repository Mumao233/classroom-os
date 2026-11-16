-- PR-07b extends the reviewed PR-07a metadata-only dispatcher and recovery functions.
CREATE OR REPLACE FUNCTION classroom_web_claim_tasks(worker text, lease_seconds integer,
    batch_size integer, requested_schema text)
RETURNS TABLE(id uuid, owner_id uuid, kind text, lease_token bigint, lease_expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $claim$
DECLARE candidate record;
        fence bigint;
        expiry timestamptz;
        sequence bigint;
BEGIN
  IF worker IS NULL OR char_length(worker) NOT BETWEEN 1 AND 100
      OR lease_seconds IS NULL OR lease_seconds NOT BETWEEN 1 AND 3600
      OR batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100
      OR requested_schema IS NULL OR requested_schema NOT IN ('1','pr07a-1','pr07b-1','pr07b-batch-1') THEN
    RAISE EXCEPTION 'invalid claim limits' USING ERRCODE = '22023';
  END IF;
  FOR candidate IN
    WITH picked AS MATERIALIZED (
      SELECT t.id, t.owner_id, t.kind, t.lease_token, t.revision,
             t.class_session_id, t.may_have_charged
      FROM public.tasks t
      WHERE t.state = 'queued' AND t.not_before <= clock_timestamp()
        AND t.schema_version = requested_schema
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
REVOKE ALL ON FUNCTION classroom_web_claim_tasks(text,integer,integer,text)
  FROM PUBLIC, classroom_web_runtime;
GRANT CREATE ON SCHEMA public TO classroom_web_task_dispatcher;
ALTER FUNCTION classroom_web_claim_tasks(text,integer,integer,text)
  OWNER TO classroom_web_task_dispatcher;
REVOKE CREATE ON SCHEMA public FROM classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_claim_tasks(text,integer,integer,text)
  TO classroom_web_scheduler;

CREATE OR REPLACE FUNCTION classroom_web_claim_tasks(worker text, lease_seconds integer,
    batch_size integer DEFAULT 1)
RETURNS TABLE(id uuid, owner_id uuid, kind text, lease_token bigint, lease_expires_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $claim$
  SELECT * FROM public.classroom_web_claim_tasks(worker,lease_seconds,batch_size,'1');
$claim$;

CREATE FUNCTION classroom_web_review_saved_candidates(batch_size integer DEFAULT 100)
RETURNS TABLE(id uuid,owner_id uuid,kind text,lease_token bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $candidates$
BEGIN
  IF batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid question reconciliation limit' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT t.id,t.owner_id,t.kind,t.lease_token FROM public.tasks t
    WHERE t.schema_version='pr07b-1' AND t.state='unknown'
      AND (t.error_code IS NULL OR t.error_code NOT IN ('BATCHES_PENDING','BATCH_RESULT_UNKNOWN'))
      AND t.not_before<=statement_timestamp()
      AND t.error_code IS DISTINCT FROM 'PROVIDER_RESULT_CONFLICT'
      AND (t.lease_expires_at IS NULL OR t.lease_expires_at<=statement_timestamp())
      AND public.classroom_web_identity_usable(t.owner_id)
      AND public.classroom_web_qa_plan_available(t.plan_id,t.owner_id)
      AND EXISTS (SELECT FROM public.task_attempts a WHERE a.owner_id=t.owner_id
        AND a.task_id=t.id AND a.raw_output_ref IS NOT NULL)
    ORDER BY t.created_at,t.id LIMIT batch_size;
END
$candidates$;
REVOKE ALL ON FUNCTION classroom_web_review_saved_candidates(integer)
  FROM PUBLIC, classroom_web_runtime;
GRANT CREATE ON SCHEMA public TO classroom_web_task_dispatcher;
ALTER FUNCTION classroom_web_review_saved_candidates(integer) OWNER TO classroom_web_task_dispatcher;
REVOKE CREATE ON SCHEMA public FROM classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_review_saved_candidates(integer) TO classroom_web_scheduler;

CREATE FUNCTION classroom_web_review_batch_saved_candidates(batch_size integer DEFAULT 100)
RETURNS TABLE(id uuid,owner_id uuid,kind text,lease_token bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $candidates$
BEGIN
  IF batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid question reconciliation limit' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT t.id,t.owner_id,t.kind,t.lease_token FROM public.tasks t
    WHERE t.schema_version='pr07b-batch-1' AND t.state='unknown'
      AND t.not_before<=statement_timestamp()
      AND t.error_code IS DISTINCT FROM 'PROVIDER_RESULT_CONFLICT'
      AND (t.lease_expires_at IS NULL OR t.lease_expires_at<=statement_timestamp())
      AND public.classroom_web_identity_usable(t.owner_id)
      AND public.classroom_web_qa_plan_available(t.plan_id,t.owner_id)
      AND EXISTS (SELECT FROM public.task_attempts a WHERE a.owner_id=t.owner_id
        AND a.task_id=t.id AND a.raw_output_ref IS NOT NULL)
    ORDER BY t.created_at,t.id LIMIT batch_size;
END
$candidates$;
REVOKE ALL ON FUNCTION classroom_web_review_batch_saved_candidates(integer)
  FROM PUBLIC, classroom_web_runtime;
GRANT CREATE ON SCHEMA public TO classroom_web_task_dispatcher;
ALTER FUNCTION classroom_web_review_batch_saved_candidates(integer) OWNER TO classroom_web_task_dispatcher;
REVOKE CREATE ON SCHEMA public FROM classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_review_batch_saved_candidates(integer) TO classroom_web_scheduler;

CREATE OR REPLACE FUNCTION classroom_web_recover_revoked(task uuid, token bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $revoked$
DECLARE candidate record;
        inaccessible boolean;
        charged boolean;
        new_state text;
        failure text;
        sequence bigint;
BEGIN
  IF task IS NULL OR token IS NULL OR token < 0 THEN
    RAISE EXCEPTION 'invalid revoked recovery identity' USING ERRCODE='22023';
  END IF;
  SELECT t.id,t.owner_id,t.kind,t.state,t.class_session_id,t.schema_version,t.plan_id,
      t.lease_token,t.lease_expires_at,t.revision,t.may_have_charged,t.error_code
    INTO candidate FROM public.tasks t WHERE t.id=task FOR UPDATE;
  IF NOT FOUND OR candidate.lease_token<>token
      OR NOT ((candidate.state IN ('running','cancel_requested') AND candidate.lease_expires_at<=clock_timestamp())
        OR (candidate.schema_version='pr07b-1' AND candidate.state IN ('running','unknown','cancel_requested')
            AND candidate.lease_expires_at IS NULL AND candidate.error_code IN (
                'BATCHES_PENDING','BATCH_RESULT_UNKNOWN','PROVIDER_RESULT_CONFLICT'))) THEN
    RETURN false;
  END IF;
  -- Monotonic quarantine only. No ancestor locks follow the task lock.
  SELECT NOT public.classroom_web_identity_usable(candidate.owner_id)
    OR EXISTS (SELECT FROM public.deletion_tombstones d
      WHERE d.owner_id=candidate.owner_id AND d.resource_type='account'
        AND d.resource_id=candidate.owner_id) INTO inaccessible;
  IF NOT inaccessible AND candidate.class_session_id IS NOT NULL THEN
    SELECT NOT EXISTS (
      SELECT FROM public.class_sessions cs
        JOIN public.courses c ON c.owner_id=cs.owner_id AND c.id=cs.course_id
        JOIN public.semesters s ON s.owner_id=c.owner_id AND s.id=c.semester_id
        JOIN public.course_material_versions m ON m.owner_id=cs.owner_id AND m.id=cs.course_material_version_id
        JOIN public.courses mc ON mc.owner_id=m.owner_id AND mc.id=m.course_id
        JOIN public.semesters ms ON ms.owner_id=mc.owner_id AND ms.id=mc.semester_id
      WHERE cs.owner_id=candidate.owner_id AND cs.id=candidate.class_session_id
        AND cs.deleted_at IS NULL AND c.deleted_at IS NULL AND s.deleted_at IS NULL
        AND mc.deleted_at IS NULL AND ms.deleted_at IS NULL
        AND NOT EXISTS (SELECT FROM public.deletion_tombstones d
          WHERE d.owner_id=candidate.owner_id AND (
            (d.resource_type='class_session' AND d.resource_id=cs.id)
            OR (d.resource_type='course' AND d.resource_id IN (c.id,mc.id))
            OR (d.resource_type='semester' AND d.resource_id IN (s.id,ms.id))
            OR (d.resource_type='course_material_version' AND d.resource_id=m.id)))
    ) INTO inaccessible;
  END IF;
  IF NOT inaccessible AND candidate.schema_version IN ('pr07a-1','pr07b-1','pr07b-batch-1') THEN
    inaccessible := NOT public.classroom_web_qa_plan_available(candidate.plan_id,candidate.owner_id);
  END IF;
  IF NOT inaccessible THEN RETURN false; END IF;
  PERFORM a.id FROM public.task_attempts a
    WHERE a.owner_id=candidate.owner_id AND a.task_id=candidate.id FOR UPDATE;
  SELECT candidate.may_have_charged OR EXISTS (
    SELECT FROM public.task_attempts a WHERE a.owner_id=candidate.owner_id
      AND a.task_id=candidate.id
      AND public.classroom_web_attempt_submitted(a.owner_id,a.id)) INTO charged;
  new_state := CASE WHEN charged THEN 'unknown' ELSE 'cancelled' END;
  failure := CASE WHEN charged THEN 'PROVIDER_RESULT_UNKNOWN' ELSE 'ACCESS_REVOKED' END;
  UPDATE public.task_attempts a SET
      state=CASE WHEN public.classroom_web_attempt_submitted(a.owner_id,a.id)
        THEN 'unknown' ELSE 'cancelled' END,
      provider_outcome=CASE WHEN a.raw_output_ref IS NOT NULL
          OR a.provider_outcome IN ('succeeded','failed') THEN a.provider_outcome
        WHEN public.classroom_web_attempt_submitted(a.owner_id,a.id)
          THEN 'unknown' ELSE 'not_submitted' END,
      error_code=failure,
      finished_at=CASE WHEN NOT public.classroom_web_attempt_submitted(a.owner_id,a.id)
        THEN coalesce(a.finished_at,clock_timestamp()) ELSE a.finished_at END
    WHERE a.owner_id=candidate.owner_id AND a.task_id=candidate.id;
  UPDATE public.tasks t SET state=new_state,error_code=failure,
      may_have_charged=charged,lease_token=t.lease_token+1,
      leased_by=NULL,lease_expires_at=NULL,revision=t.revision+1,
      updated_at=clock_timestamp(),completed_at=CASE WHEN charged THEN NULL ELSE clock_timestamp() END
    WHERE t.id=candidate.id AND t.owner_id=candidate.owner_id;
  INSERT INTO public.owner_event_counters(id,owner_id,next_sequence)
    VALUES(gen_random_uuid(),candidate.owner_id,1) ON CONFLICT DO NOTHING;
  UPDATE public.owner_event_counters c SET next_sequence=c.next_sequence+1
    WHERE c.owner_id=candidate.owner_id RETURNING c.next_sequence-1 INTO sequence;
  INSERT INTO public.outbox_events(id,owner_id,event_sequence,event_type,
      aggregate_type,aggregate_id,aggregate_revision,payload,schema_version)
    VALUES(gen_random_uuid(),candidate.owner_id,sequence,'task.changed','task',candidate.id,
      candidate.revision+1,jsonb_build_object('task_id',candidate.id,
        'class_session_id',candidate.class_session_id,'revision',candidate.revision+1,
        'state',new_state,'error_code',failure,'may_have_charged',charged),'1');
  RETURN true;
END
$revoked$;
ALTER POLICY question_plan_live ON public.tasks
  USING (schema_version NOT IN ('pr07a-1','pr07b-1','pr07b-batch-1') OR public.classroom_web_qa_plan_live(plan_id))
  WITH CHECK (schema_version NOT IN ('pr07a-1','pr07b-1','pr07b-batch-1') OR public.classroom_web_qa_plan_live(plan_id));

CREATE FUNCTION classroom_web_review_pending(batch_size integer DEFAULT 32)
RETURNS TABLE(id uuid,owner_id uuid,kind text,lease_token bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $pending$
BEGIN
 IF batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'invalid review coordination limit' USING ERRCODE='22023';
 END IF;
 -- Rotate the metadata scheduling timestamp even when an unknown batch cannot
 -- progress. A bounded set of uncertain calls must not starve other owners.
 RETURN QUERY WITH picked AS MATERIALIZED (
  SELECT t.id,t.owner_id FROM public.tasks t
   WHERE t.schema_version='pr07b-1' AND t.state IN ('running','unknown','cancel_requested')
     AND t.error_code IN ('BATCHES_PENDING','BATCH_RESULT_UNKNOWN','PROVIDER_RESULT_CONFLICT')
     AND t.lease_expires_at IS NULL AND t.not_before<=statement_timestamp()
   ORDER BY t.not_before,t.created_at,t.id LIMIT batch_size FOR UPDATE SKIP LOCKED
 ) UPDATE public.tasks t SET not_before=clock_timestamp()
   FROM picked p WHERE t.id=p.id AND t.owner_id=p.owner_id
   RETURNING t.id,t.owner_id,t.kind,t.lease_token;
END
$pending$;
REVOKE ALL ON FUNCTION classroom_web_review_pending(integer) FROM PUBLIC,classroom_web_runtime;
GRANT CREATE ON SCHEMA public TO classroom_web_task_dispatcher;
ALTER FUNCTION classroom_web_review_pending(integer) OWNER TO classroom_web_task_dispatcher;
REVOKE CREATE ON SCHEMA public FROM classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_review_pending(integer) TO classroom_web_scheduler;
GRANT UPDATE (not_before) ON tasks TO classroom_web_task_dispatcher;

-- Preserve execution guards while allowing local-only multi-batch recovery.
CREATE OR REPLACE FUNCTION classroom_web_guard_execution() RETURNS trigger
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
      -- A review parent resumes local coordination after its first result is
      -- durable. Its old Attempt cannot be sent again and it receives no lease.
      IF NEW.schema_version = 'pr07b-1' AND NEW.state = 'running'
          AND NEW.error_code = 'BATCHES_PENDING' AND NEW.leased_by IS NULL
          AND NEW.lease_expires_at IS NULL THEN
        IF NOT EXISTS (SELECT FROM public.review_batches b
            JOIN public.task_attempts a ON a.owner_id=b.owner_id AND a.task_id=b.task_id
            JOIN public.provider_results r ON r.owner_id=a.owner_id AND r.attempt_id=a.id
            WHERE b.owner_id=NEW.owner_id AND b.plan_id=NEW.plan_id AND b.batch_index=0
              AND b.task_id=NEW.id AND r.validation_status='valid' AND r.conflict_sha256 IS NULL)
            OR NOT EXISTS (SELECT FROM public.review_batches b WHERE b.owner_id=NEW.owner_id
              AND b.plan_id=NEW.plan_id AND b.batch_index>0)
            OR EXISTS (SELECT FROM public.review_batches b
              JOIN public.task_attempts a ON a.owner_id=b.owner_id AND a.task_id=b.task_id
              JOIN public.provider_results r ON r.owner_id=a.owner_id AND r.attempt_id=a.id
              WHERE b.owner_id=NEW.owner_id AND b.plan_id=NEW.plan_id AND r.conflict_sha256 IS NOT NULL) THEN
          RAISE EXCEPTION 'review coordination requires a durable unconflicted first result' USING ERRCODE = '23514';
        END IF;
      ELSE
        RAISE EXCEPTION 'unknown task cannot be automatically resubmitted' USING ERRCODE = '23514';
      END IF;
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
