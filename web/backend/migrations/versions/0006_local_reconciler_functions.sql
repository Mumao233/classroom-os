-- 0006 body corrections. Executed before the ownership/ACL part of this same
-- revision, so a provisioned non-superuser can replace its existing functions.
-- No raw content crosses either fixed entry point, including on exceptions.

CREATE OR REPLACE FUNCTION classroom_web_local_reconcile_candidates(batch_size integer DEFAULT 100)
RETURNS TABLE(id uuid, owner_id uuid, kind text, lease_token bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp
SET statement_timeout = '15s' AS $candidates$
BEGIN
  IF batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid local reconciliation batch limit' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY SELECT t.id, t.owner_id, t.kind, t.lease_token
    FROM public.tasks t
    CROSS JOIN LATERAL (
      SELECT a.raw_output_ref, r.id AS result_id, r.validation_status, r.conflict_sha256
      FROM public.task_attempts a JOIN public.provider_results r
        ON r.owner_id = a.owner_id AND r.attempt_id = a.id
      WHERE a.owner_id = t.owner_id AND a.task_id = t.id
      ORDER BY a.attempt_number DESC, a.id DESC LIMIT 1
    ) AS saved
    WHERE t.state = 'unknown' AND t.not_before <= statement_timestamp()
      AND t.error_code IS DISTINCT FROM 'ACCESS_REVOKED'
      AND (t.lease_expires_at IS NULL OR t.lease_expires_at <= statement_timestamp())
      AND NOT public.classroom_web_identity_usable(t.owner_id)
      AND saved.validation_status = 'pending' AND saved.conflict_sha256 IS NULL
      AND (saved.raw_output_ref IS NULL OR saved.raw_output_ref = saved.result_id)
    ORDER BY t.updated_at, t.id LIMIT batch_size;
EXCEPTION WHEN OTHERS THEN
  RAISE EXCEPTION 'local reconciliation queue failed' USING ERRCODE = SQLSTATE;
END
$candidates$;

CREATE OR REPLACE FUNCTION classroom_web_reconcile_saved_result(task uuid, token bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
SET statement_timeout = '15s' AS $finish$
DECLARE candidate record;
        initial_class record;
        locked_class record;
        evidence record;
        saved record;
        principal uuid;
        course_ids uuid[];
        original_semesters uuid[];
        locked_semesters uuid[];
        semester_ids uuid[];
        material_id uuid;
        material_course uuid;
        revoked boolean := false;
        payload jsonb;
        output_valid boolean;
        new_state text;
        failure text;
        sequence bigint;
        row_count integer;
BEGIN
  principal := nullif(current_setting('app.owner_id', true), '')::uuid;
  IF task IS NULL OR token IS NULL OR token < 0 OR principal IS NULL THEN RETURN false; END IF;
  -- GUC limits the row domain; it does not establish authentication. Only the
  -- fixed function and its isolated execution role confer this system action.
  SELECT t.id, t.owner_id, t.class_session_id INTO candidate FROM public.tasks t
    WHERE t.id = task AND t.owner_id = principal;
  IF NOT FOUND THEN RETURN false; END IF;

  -- Discover all persistent source ancestors WITHOUT a task lock. Ordinary
  -- publish/revocation takes ancestors before task; the reverse would deadlock.
  IF candidate.class_session_id IS NOT NULL THEN
    SELECT cs.id, cs.course_id, cs.course_material_version_id
      INTO initial_class FROM public.class_sessions cs
      WHERE cs.owner_id = principal AND cs.id = candidate.class_session_id;
    IF NOT FOUND THEN
      revoked := true;
    ELSE
      material_id := initial_class.course_material_version_id;
      SELECT m.course_id INTO material_course FROM public.course_material_versions m
        WHERE m.owner_id = principal AND m.id = material_id;
      IF NOT FOUND THEN
        revoked := true;
      ELSE
        SELECT array_agg(v ORDER BY v) INTO course_ids
          FROM (SELECT DISTINCT unnest(ARRAY[initial_class.course_id, material_course]) AS v) ids;
        SELECT array_agg(c.semester_id ORDER BY c.id) INTO original_semesters
          FROM public.courses c WHERE c.owner_id = principal AND c.id = ANY(course_ids);
        IF cardinality(original_semesters) IS DISTINCT FROM cardinality(course_ids) THEN
          revoked := true;
        ELSE
          SELECT array_agg(v ORDER BY v) INTO semester_ids
            FROM (SELECT DISTINCT unnest(original_semesters) AS v) ids;
          PERFORM s.id FROM public.semesters s WHERE s.owner_id = principal
            AND s.id = ANY(semester_ids) ORDER BY s.id FOR SHARE;
          GET DIAGNOSTICS row_count = ROW_COUNT;
          IF row_count <> cardinality(semester_ids) THEN revoked := true; END IF;
          PERFORM c.id FROM public.courses c WHERE c.owner_id = principal
            AND c.id = ANY(course_ids) ORDER BY c.id FOR SHARE;
          GET DIAGNOSTICS row_count = ROW_COUNT;
          IF row_count <> cardinality(course_ids) THEN revoked := true; END IF;
          SELECT array_agg(c.semester_id ORDER BY c.id) INTO locked_semesters
            FROM public.courses c WHERE c.owner_id = principal AND c.id = ANY(course_ids);
          -- A concurrent move changed the discovered graph. No transition is
          -- committed; the next bounded poll rediscovers the complete graph.
          IF locked_semesters IS DISTINCT FROM original_semesters THEN RETURN false; END IF;
          SELECT cs.id, cs.course_id, cs.course_material_version_id, cs.deleted_at
            INTO locked_class FROM public.class_sessions cs WHERE cs.owner_id = principal
              AND cs.id = candidate.class_session_id FOR SHARE;
          IF NOT FOUND THEN
            revoked := true;
          ELSIF locked_class.course_id IS DISTINCT FROM initial_class.course_id
              OR locked_class.course_material_version_id IS DISTINCT FROM material_id THEN
            RETURN false;
          ELSE
            revoked := revoked OR locked_class.deleted_at IS NOT NULL;
          END IF;
          SELECT revoked OR EXISTS (SELECT FROM public.courses c
              WHERE c.owner_id = principal AND c.id = ANY(course_ids) AND c.deleted_at IS NOT NULL)
            OR EXISTS (SELECT FROM public.semesters s WHERE s.owner_id = principal
              AND s.id = ANY(semester_ids) AND s.deleted_at IS NOT NULL) INTO revoked;
        END IF;
      END IF;
    END IF;
  END IF;
  SELECT revoked OR EXISTS (SELECT FROM public.deletion_tombstones d
    WHERE d.owner_id = principal AND (
      (d.resource_type = 'account' AND d.resource_id = principal)
      OR (d.resource_type = 'class_session' AND d.resource_id = candidate.class_session_id)
      OR (d.resource_type = 'course_material_version' AND d.resource_id = material_id)
      OR (d.resource_type = 'course' AND d.resource_id = ANY(course_ids))
      OR (d.resource_type = 'semester' AND d.resource_id = ANY(semester_ids)))) INTO revoked;

  SELECT t.id, t.owner_id, t.kind, t.state, t.lease_token, t.lease_expires_at,
      t.revision, t.class_session_id, t.not_before, t.error_code
    INTO candidate FROM public.tasks t WHERE t.id = task AND t.owner_id = principal FOR UPDATE;
  IF NOT FOUND OR candidate.lease_token <> token OR candidate.state <> 'unknown'
      OR candidate.error_code = 'ACCESS_REVOKED'
      OR candidate.not_before > clock_timestamp()
      OR (candidate.lease_expires_at IS NOT NULL AND candidate.lease_expires_at > clock_timestamp())
      OR public.classroom_web_identity_usable(candidate.owner_id) THEN RETURN false; END IF;
  PERFORM a.id FROM public.task_attempts a
    WHERE a.owner_id = principal AND a.task_id = candidate.id
    ORDER BY a.attempt_number, a.id FOR UPDATE;
  SELECT a.id AS attempt_id, a.raw_output_ref, r.id AS result_id
    INTO evidence FROM public.task_attempts a JOIN public.provider_results r
      ON r.owner_id = a.owner_id AND r.attempt_id = a.id
    WHERE a.owner_id = principal AND a.task_id = candidate.id
    ORDER BY a.attempt_number DESC, a.id DESC LIMIT 1;
  IF NOT FOUND OR (evidence.raw_output_ref IS NOT NULL
      AND evidence.raw_output_ref <> evidence.result_id) THEN RETURN false; END IF;
  SELECT r.id, r.owner_id, r.attempt_id, r.raw_output, r.validation_status, r.conflict_sha256
    INTO saved FROM public.provider_results r
    WHERE r.id = evidence.result_id AND r.owner_id = principal
      AND r.attempt_id = evidence.attempt_id FOR UPDATE;
  IF NOT FOUND OR saved.raw_output IS NULL OR saved.validation_status <> 'pending'
      OR saved.conflict_sha256 IS NOT NULL THEN RETURN false; END IF;

  IF revoked THEN
    -- Explicit quarantine is not output validation: return false, keep raw
    -- pending and charge risk unknown, and remove this candidate from polling.
    new_state := 'unknown';
    failure := 'ACCESS_REVOKED';
    UPDATE public.task_attempts a SET state = 'unknown', error_code = failure
      WHERE a.id = evidence.attempt_id AND a.owner_id = principal AND a.task_id = candidate.id;
  ELSE
    payload := public.classroom_web_local_json_payload(saved.raw_output);
    -- Evaluate the actual final JSONB representation, exactly as the existing
    -- CHECK does, before a failing row can enter PostgreSQL's error diagnostics.
    IF payload IS NOT NULL AND pg_column_size(payload) > 1048576 THEN payload := NULL; END IF;
    output_valid := payload IS NOT NULL;
    new_state := CASE WHEN output_valid THEN 'succeeded' ELSE 'failed' END;
    failure := CASE WHEN output_valid THEN NULL ELSE 'OUTPUT_INVALID' END;
    UPDATE public.provider_results r SET
        validation_status = CASE WHEN output_valid THEN 'valid' ELSE 'invalid' END,
        validated_payload = payload WHERE r.id = saved.id AND r.owner_id = principal;
    UPDATE public.task_attempts a SET state = new_state, raw_output_ref = saved.id,
        provider_outcome = CASE WHEN a.raw_output_ref IS NULL THEN 'succeeded' ELSE a.provider_outcome END,
        error_code = failure, finished_at = coalesce(a.finished_at, clock_timestamp())
      WHERE a.id = evidence.attempt_id AND a.owner_id = principal AND a.task_id = candidate.id;
  END IF;
  UPDATE public.tasks t SET state = new_state, error_code = failure,
      may_have_charged = true, lease_token = t.lease_token + 1,
      leased_by = NULL, lease_expires_at = NULL, revision = t.revision + 1,
      updated_at = clock_timestamp(),
      completed_at = CASE WHEN revoked THEN NULL ELSE clock_timestamp() END
    WHERE t.id = candidate.id AND t.owner_id = principal;
  INSERT INTO public.owner_event_counters(id, owner_id, next_sequence)
    VALUES(gen_random_uuid(), principal, 1) ON CONFLICT DO NOTHING;
  UPDATE public.owner_event_counters c SET next_sequence = c.next_sequence + 1
    WHERE c.owner_id = principal RETURNING c.next_sequence - 1 INTO sequence;
  INSERT INTO public.outbox_events(id, owner_id, event_sequence, event_type,
      aggregate_type, aggregate_id, aggregate_revision, payload, schema_version)
    VALUES(gen_random_uuid(), principal, sequence, 'task.changed', 'task', candidate.id,
      candidate.revision + 1, jsonb_build_object('task_id', candidate.id,
        'class_session_id', candidate.class_session_id, 'revision', candidate.revision + 1,
        'state', new_state, 'error_code', failure, 'may_have_charged', true), '1');
  RETURN NOT revoked;
EXCEPTION WHEN query_canceled OR assert_failure THEN
  RAISE EXCEPTION 'local reconciliation interrupted' USING ERRCODE = SQLSTATE;
WHEN OTHERS THEN
  -- This handler rolls back ALL inner writes, and deliberately does not copy
  -- MESSAGE/DETAIL/HINT/CONTEXT or chain the original private row diagnostics.
  -- Preserve SQLSTATE so contention and existing rollback assertions still work.
  RAISE EXCEPTION 'local reconciliation failed' USING ERRCODE = SQLSTATE;
END
$finish$;
