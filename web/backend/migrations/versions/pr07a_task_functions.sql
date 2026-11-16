-- PR-07a dispatch isolation. Existing callers keep the three-argument function.
-- The dispatcher still receives routing metadata only, never question bodies.
GRANT SELECT (schema_version) ON public.tasks TO classroom_web_task_dispatcher;

CREATE FUNCTION classroom_web_claim_tasks(worker text, lease_seconds integer,
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
      OR requested_schema IS NULL OR requested_schema NOT IN ('1','pr07a-1') THEN
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

-- Generic local reconciliation cannot validate question citations or atomically
-- persist their answer. Restrictive policies also close direct function calls.
GRANT SELECT (schema_version) ON public.tasks
  TO classroom_web_recovery_metadata, classroom_web_local_result_processor;
CREATE POLICY generic_recovery_schema ON public.tasks AS RESTRICTIVE
  TO classroom_web_recovery_metadata, classroom_web_local_result_processor
  USING (schema_version = '1') WITH CHECK (schema_version = '1');

-- Source revocation applies to stored provider text as well as answer queries.
-- Plan visibility is independent of tasks, so this policy cannot recurse.
CREATE POLICY question_plan_live ON public.tasks AS RESTRICTIVE
  TO classroom_web_runtime
  USING (schema_version <> 'pr07a-1' OR public.classroom_web_qa_plan_live(plan_id))
  WITH CHECK (schema_version <> 'pr07a-1' OR public.classroom_web_qa_plan_live(plan_id));
CREATE POLICY question_task_live ON public.task_attempts AS RESTRICTIVE
  TO classroom_web_runtime
  USING (EXISTS (SELECT FROM public.tasks t
    WHERE t.owner_id=task_attempts.owner_id AND t.id=task_attempts.task_id))
  WITH CHECK (EXISTS (SELECT FROM public.tasks t
    WHERE t.owner_id=task_attempts.owner_id AND t.id=task_attempts.task_id));
CREATE POLICY question_result_live ON public.provider_results AS RESTRICTIVE
  TO classroom_web_runtime
  USING (EXISTS (SELECT FROM public.task_attempts a
    WHERE a.owner_id=provider_results.owner_id AND a.id=provider_results.attempt_id))
  WITH CHECK (EXISTS (SELECT FROM public.task_attempts a
    WHERE a.owner_id=provider_results.owner_id AND a.id=provider_results.attempt_id));

-- Revocation recovery can inspect source liveness without receiving source,
-- question, snapshot text, provider output, or billing amounts.
GRANT SELECT (plan_id) ON public.tasks TO classroom_web_task_dispatcher;
GRANT SELECT (id,owner_id,snapshot_id) ON public.generation_plans TO classroom_web_task_dispatcher;
GRANT SELECT (id,owner_id,class_session_id,source_ids) ON public.source_snapshots TO classroom_web_task_dispatcher;
GRANT SELECT (id,owner_id,class_session_id,revoked_at) ON public.qa_sources TO classroom_web_task_dispatcher;
CREATE POLICY qa_dispatch_metadata ON public.generation_plans
  FOR SELECT TO classroom_web_task_dispatcher USING (true);
CREATE POLICY qa_dispatch_metadata ON public.source_snapshots
  FOR SELECT TO classroom_web_task_dispatcher USING (true);
CREATE POLICY qa_dispatch_metadata ON public.qa_sources
  FOR SELECT TO classroom_web_task_dispatcher USING (true);

CREATE FUNCTION classroom_web_qa_plan_available(plan uuid, principal uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=public,pg_temp AS $available$
  SELECT EXISTS (
    SELECT FROM public.generation_plans p JOIN public.source_snapshots s
      ON s.owner_id=p.owner_id AND s.id=p.snapshot_id
      JOIN public.class_sessions cs ON cs.owner_id=s.owner_id AND cs.id=s.class_session_id
      JOIN public.courses c ON c.owner_id=cs.owner_id AND c.id=cs.course_id
      JOIN public.semesters e ON e.owner_id=c.owner_id AND e.id=c.semester_id
      JOIN public.course_material_versions m ON m.owner_id=cs.owner_id AND m.id=cs.course_material_version_id
      JOIN public.courses mc ON mc.owner_id=m.owner_id AND mc.id=m.course_id
      JOIN public.semesters me ON me.owner_id=mc.owner_id AND me.id=mc.semester_id
    WHERE p.id=plan AND p.owner_id=principal AND cardinality(s.source_ids)>0
      AND cs.deleted_at IS NULL AND c.deleted_at IS NULL AND e.deleted_at IS NULL
      AND mc.deleted_at IS NULL AND me.deleted_at IS NULL
      AND NOT EXISTS (SELECT FROM public.deletion_tombstones d WHERE d.owner_id=principal AND (
        (d.resource_type='account' AND d.resource_id=principal)
        OR (d.resource_type='class_session' AND d.resource_id=cs.id)
        OR (d.resource_type='course_material_version' AND d.resource_id=m.id)
        OR (d.resource_type='course' AND d.resource_id IN (c.id,mc.id))
        OR (d.resource_type='semester' AND d.resource_id IN (e.id,me.id))))
      AND NOT EXISTS (SELECT FROM unnest(s.source_ids) AS source(id)
        WHERE NOT EXISTS (SELECT FROM public.qa_sources q
          WHERE q.owner_id=principal AND q.id=source.id
            AND q.class_session_id=s.class_session_id AND q.revoked_at IS NULL)));
$available$;
REVOKE ALL ON FUNCTION classroom_web_qa_plan_available(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_qa_plan_available(uuid,uuid)
  TO classroom_web_task_dispatcher;

CREATE FUNCTION classroom_web_qa_saved_candidates(batch_size integer DEFAULT 100)
RETURNS TABLE(id uuid,owner_id uuid,kind text,lease_token bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $candidates$
BEGIN
  IF batch_size IS NULL OR batch_size NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'invalid question reconciliation limit' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT t.id,t.owner_id,t.kind,t.lease_token FROM public.tasks t
    WHERE t.schema_version='pr07a-1' AND t.state='unknown'
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
REVOKE ALL ON FUNCTION classroom_web_qa_saved_candidates(integer)
  FROM PUBLIC, classroom_web_runtime;
GRANT CREATE ON SCHEMA public TO classroom_web_task_dispatcher;
ALTER FUNCTION classroom_web_qa_saved_candidates(integer) OWNER TO classroom_web_task_dispatcher;
REVOKE CREATE ON SCHEMA public FROM classroom_web_task_dispatcher;
GRANT EXECUTE ON FUNCTION classroom_web_qa_saved_candidates(integer) TO classroom_web_scheduler;

-- Frozen 0005 recovery body with the additional QA source-liveness predicate.
-- Ownership/ACL remain the existing isolated dispatcher and scheduler boundary.
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
      t.lease_token,t.lease_expires_at,t.revision,t.may_have_charged
    INTO candidate FROM public.tasks t WHERE t.id=task FOR UPDATE;
  IF NOT FOUND OR candidate.lease_token<>token
      OR candidate.state NOT IN ('running','cancel_requested')
      OR candidate.lease_expires_at IS NULL OR candidate.lease_expires_at>clock_timestamp() THEN
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
  IF NOT inaccessible AND candidate.schema_version='pr07a-1' THEN
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
