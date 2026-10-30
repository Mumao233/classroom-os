-- Additive PR-03a recovery remediation. Never rewrite accepted migrations.
-- Helpers return booleans only: the scheduler gains no user fields, provider
-- request IDs, result bodies or direct table access. The fixed definer helpers
-- read existing metadata internally; only the existing dispatcher can call them.

CREATE FUNCTION classroom_web_identity_usable(principal_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $usable$
  SELECT EXISTS (SELECT FROM public.users
    WHERE id = principal_id AND state = 'active' AND deleted_at IS NULL
      AND (normalized_email IS NULL
        OR (email_verified_at IS NOT NULL AND password_hash IS NOT NULL)))
$usable$;
REVOKE ALL ON FUNCTION classroom_web_identity_usable(uuid) FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_identity;
GRANT EXECUTE ON FUNCTION classroom_web_identity_usable(uuid)
  TO classroom_web_task_dispatcher;

-- Share the account predicate with RLS's existing fixed definer wrapper. Its
-- signature, owner and ACL survive CREATE OR REPLACE. lock_owner intentionally
-- keeps 0004's explicit users row predicate and FOR SHARE: EvalPlanQual must
-- recheck current row fields after waiting for an account update.
CREATE OR REPLACE FUNCTION classroom_web_owner_active() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $owner$
  SELECT public.classroom_web_identity_usable(
    nullif(current_setting('app.owner_id', true), '')::uuid)
$owner$;

-- Match PgTaskRepository._submitted without granting the dispatcher a readable
-- provider_request_id column. A positive result is risk evidence, not a claim
-- that an actual external request or charge definitely occurred.
CREATE FUNCTION classroom_web_attempt_submitted(principal_id uuid, attempt_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $submitted$
  SELECT EXISTS (SELECT FROM public.task_attempts a
    WHERE a.owner_id = principal_id AND a.id = attempt_id AND (
      a.submitted_at IS NOT NULL OR a.provider_request_id IS NOT NULL
      OR a.raw_output_ref IS NOT NULL
      OR a.provider_outcome IN ('succeeded','failed','unknown')
      OR a.state NOT IN ('planned','cancelled')
      OR EXISTS (SELECT FROM public.provider_results r
        WHERE r.owner_id = a.owner_id AND r.attempt_id = a.id)))
$submitted$;
REVOKE ALL ON FUNCTION classroom_web_attempt_submitted(uuid, uuid) FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_identity;
GRANT EXECUTE ON FUNCTION classroom_web_attempt_submitted(uuid, uuid)
  TO classroom_web_task_dispatcher;

-- Preserve the accepted fallback's lineage/tombstone checks, lock order,
-- fence/state/expiry guards and atomic minimized event. Recovery makes only
-- monotonic cancellation/quarantine changes. It never reads/publishes raw
-- result text or invokes a Provider. Persisted raw stays isolated for a later
-- separately authorized local reconciliation phase.
CREATE OR REPLACE FUNCTION classroom_web_recover_revoked(task uuid, token bigint) RETURNS boolean
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
  SELECT NOT public.classroom_web_identity_usable(candidate.owner_id)
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
      AND a.task_id = candidate.id
      AND public.classroom_web_attempt_submitted(a.owner_id, a.id)) INTO charged;
  new_state := CASE WHEN charged THEN 'unknown' ELSE 'cancelled' END;
  failure := CASE WHEN charged THEN 'PROVIDER_RESULT_UNKNOWN' ELSE 'ACCESS_REVOKED' END;
  UPDATE public.task_attempts a SET
      state = CASE WHEN public.classroom_web_attempt_submitted(a.owner_id, a.id)
        THEN 'unknown' ELSE 'cancelled' END,
      provider_outcome = CASE WHEN a.raw_output_ref IS NOT NULL
          OR a.provider_outcome IN ('succeeded','failed') THEN a.provider_outcome
        WHEN public.classroom_web_attempt_submitted(a.owner_id, a.id)
          THEN 'unknown' ELSE 'not_submitted' END,
      error_code = failure,
      finished_at = CASE WHEN NOT public.classroom_web_attempt_submitted(a.owner_id, a.id)
        THEN coalesce(a.finished_at, clock_timestamp()) ELSE a.finished_at END
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

-- CREATE OR REPLACE retains classroom_web_task_dispatcher ownership and the
-- existing scheduler EXECUTE ACL. No scheduler grant or RLS policy is changed.

-- APPROVED SYSTEM PROCESSING BOUNDARY: local validation of already persisted
-- raw results for unusable identities. This is not an authentication fallback.
-- Pure helpers are private to the migration principal; neither runtime nor the
-- scheduler can call a body-returning helper. Only the two final fixed entry
-- points are executable by the trusted runtime's internal LocalReconciler.

CREATE FUNCTION classroom_web_local_float_token(token text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path = public, pg_temp
SET extra_float_digits = 3 AS $float$
DECLARE value double precision;
        pieces text[];
        integer_part text;
        fraction_part text;
        exponent_text text;
        exponent_digits text;
        exponent_sign integer;
        exponent_value integer;
        magnitude integer;
        coefficient text;
        first_nonzero integer;
        scientific_exponent integer;
        negative boolean := left(token, 1) = '-';
        rendered text;
BEGIN
  -- Python accepts floating underflow as signed zero but rejects nonfinite
  -- serialization. PostgreSQL reports a range error for zero underflow, so
  -- classify the original decimal magnitude without an unbounded numeric cast.
  BEGIN
    value := token::double precision;
  EXCEPTION WHEN numeric_value_out_of_range THEN
    pieces := regexp_match(ltrim(token, '-'),
      '^([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$');
    IF pieces IS NULL THEN
      RAISE EXCEPTION 'invalid local JSON number' USING ERRCODE = '22023';
    END IF;
    integer_part := pieces[1];
    fraction_part := coalesce(pieces[2], '');
    exponent_text := coalesce(pieces[3], '0');
    exponent_sign := CASE WHEN left(exponent_text, 1) = '-' THEN -1 ELSE 1 END;
    exponent_digits := ltrim(ltrim(exponent_text, '+-'), '0');
    exponent_value := exponent_sign * CASE
      WHEN length(exponent_digits) > 6 THEN 1000000
      ELSE coalesce(nullif(exponent_digits, ''), '0')::integer END;
    IF ltrim(integer_part || fraction_part, '0') = '' THEN
      RETURN CASE WHEN negative THEN '-0.0' ELSE '0.0' END;
    ELSIF ltrim(integer_part, '0') <> '' THEN
      magnitude := exponent_value + length(ltrim(integer_part, '0')) - 1;
    ELSE
      magnitude := exponent_value -
        (length(fraction_part) - length(ltrim(fraction_part, '0')) + 1);
    END IF;
    IF magnitude < 0 THEN
      RETURN CASE WHEN negative THEN '-0.0' ELSE '0.0' END;
    END IF;
    RAISE EXCEPTION 'nonfinite local JSON number' USING ERRCODE = '22023';
  END;
  IF value::text IN ('NaN','Infinity','-Infinity') THEN
    RAISE EXCEPTION 'nonfinite local JSON number' USING ERRCODE = '22023';
  END IF;
  IF value = 0 THEN
    RETURN CASE WHEN negative THEN '-0.0' ELSE '0.0' END;
  END IF;

  -- PostgreSQL's shortest IEEE-754 representation supplies the coefficient.
  -- Normalize its notation to Python repr(float): fixed for exponents -4..15,
  -- '.0' for integral fixed floats, and at least two exponent digits.
  pieces := regexp_match(ltrim(value::text, '-'),
    '^([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$');
  IF pieces IS NULL THEN
    RAISE EXCEPTION 'invalid local float rendering' USING ERRCODE = '22023';
  END IF;
  integer_part := pieces[1];
  coefficient := integer_part || coalesce(pieces[2], '');
  first_nonzero := length(coefficient) - length(ltrim(coefficient, '0')) + 1;
  scientific_exponent := coalesce(pieces[3], '0')::integer
    + length(integer_part) - first_nonzero;
  coefficient := rtrim(ltrim(coefficient, '0'), '0');
  IF scientific_exponent BETWEEN -4 AND 15 THEN
    IF scientific_exponent < 0 THEN
      rendered := '0.' || repeat('0', -scientific_exponent - 1) || coefficient;
    ELSIF length(coefficient) <= scientific_exponent + 1 THEN
      rendered := coefficient || repeat('0', scientific_exponent + 1 - length(coefficient)) || '.0';
    ELSE
      rendered := substr(coefficient, 1, scientific_exponent + 1)
        || '.' || substr(coefficient, scientific_exponent + 2);
    END IF;
  ELSE
    rendered := left(coefficient, 1)
      || CASE WHEN length(coefficient) > 1 THEN '.' || substr(coefficient, 2) ELSE '' END
      || 'e' || CASE WHEN scientific_exponent < 0 THEN '-' ELSE '+' END
      || lpad(abs(scientific_exponent)::text,
              greatest(2, length(abs(scientific_exponent)::text)), '0');
  END IF;
  RETURN CASE WHEN negative THEN '-' ELSE '' END || rendered;
END
$float$;
REVOKE ALL ON FUNCTION classroom_web_local_float_token(text) FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_task_dispatcher,
  classroom_web_identity;

CREATE FUNCTION classroom_web_local_json_token(token text)
RETURNS TABLE(normalized text, serialized_size bigint)
LANGUAGE plpgsql IMMUTABLE SET search_path = public, pg_temp AS $json_token$
DECLARE decoded text;
BEGIN
  normalized := token;
  IF left(token, 1) = '"' THEN
    decoded := token::jsonb #>> '{}';
    -- A streaming character split avoids repeated UTF-8 substring scans.
    -- Python ensure_ascii uses six bytes for BMP codepoints and twelve for
    -- supplementary surrogate pairs, including in object keys.
    SELECT 2 + coalesce(sum(CASE
      WHEN ascii(ch) IN (8,9,10,12,13,34,92) THEN 2
      WHEN ascii(ch) BETWEEN 32 AND 126 THEN 1
      WHEN ascii(ch) <= 65535 THEN 6
      ELSE 12 END), 0)
      INTO serialized_size
      FROM regexp_split_to_table(decoded, '') AS characters(ch) WHERE ch <> '';
  ELSIF left(token, 1) ~ '[-0-9]' THEN
    IF token ~ '[.eE]' THEN
      normalized := public.classroom_web_local_float_token(token);
    ELSE
      -- Python 3.12's standard int conversion bound is 4300 decimal digits.
      IF length(ltrim(token, '-')) > 4300 THEN
        RAISE EXCEPTION 'local JSON integer exceeds conversion bound' USING ERRCODE = '22023';
      END IF;
      IF token = '-0' THEN normalized := '0'; END IF;
    END IF;
    serialized_size := length(normalized);
  ELSIF token IN (',', ':') THEN
    serialized_size := 2;  -- Python json.dumps default ', ' and ': '.
  ELSE
    serialized_size := length(token);
  END IF;
  RETURN NEXT;
END
$json_token$;
REVOKE ALL ON FUNCTION classroom_web_local_json_token(text) FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_task_dispatcher,
  classroom_web_identity;

CREATE FUNCTION classroom_web_local_json_payload(raw text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = public, pg_temp AS $json_payload$
DECLARE normalized text;
        serialized_size bigint;
BEGIN
  IF raw IS NULL OR char_length(raw) > 200000
      OR NOT (raw IS JSON OBJECT WITH UNIQUE KEYS) THEN
    RETURN NULL;
  END IF;
  -- Parse once for strict nested uniqueness, then use a lexical stream instead
  -- of recursive SQL calls. Whitespace is normalized separately for exact
  -- Python default serialized-size accounting, never jsonb storage-size guesses.
  SELECT string_agg(piece.normalized, '' ORDER BY tokens.ordinality),
      sum(piece.serialized_size)
    INTO normalized, serialized_size
    FROM regexp_matches(raw,
      $token_re$("(?:[^"\\]|\\.)*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|\{|\}|\[|\]|,|:)$token_re$,
      'g') WITH ORDINALITY AS tokens(matches, ordinality)
    CROSS JOIN LATERAL public.classroom_web_local_json_token(tokens.matches[1]) AS piece;
  IF serialized_size IS NULL OR serialized_size > 800000 THEN
    RETURN NULL;
  END IF;
  RETURN normalized::jsonb;
EXCEPTION WHEN data_exception OR program_limit_exceeded THEN
  -- Invalid structure/encoding/nonfinite numbers and bounded parser failures
  -- are invalid output. Permission, infrastructure and later mutation failures
  -- are not swallowed: the complete reconciliation transaction must roll back.
  RETURN NULL;
END
$json_payload$;
REVOKE ALL ON FUNCTION classroom_web_local_json_payload(text) FROM PUBLIC,
  classroom_web_runtime, classroom_web_scheduler, classroom_web_task_dispatcher,
  classroom_web_identity;

CREATE FUNCTION classroom_web_local_reconcile_candidates(batch_size integer DEFAULT 100)
RETURNS TABLE(id uuid, owner_id uuid, kind text, lease_token bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp
SET statement_timeout = '15s' AS $local_candidates$
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
      AND (t.lease_expires_at IS NULL OR t.lease_expires_at <= statement_timestamp())
      AND NOT public.classroom_web_identity_usable(t.owner_id)
      AND saved.validation_status = 'pending' AND saved.conflict_sha256 IS NULL
      AND (saved.raw_output_ref IS NULL OR saved.raw_output_ref = saved.result_id)
    ORDER BY t.updated_at, t.id LIMIT batch_size;
END
$local_candidates$;
REVOKE ALL ON FUNCTION classroom_web_local_reconcile_candidates(integer) FROM PUBLIC,
  classroom_web_scheduler, classroom_web_task_dispatcher, classroom_web_identity;
GRANT EXECUTE ON FUNCTION classroom_web_local_reconcile_candidates(integer)
  TO classroom_web_runtime;

CREATE FUNCTION classroom_web_reconcile_saved_result(task uuid, token bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
SET statement_timeout = '15s' AS $local_finish$
DECLARE candidate record;
        evidence record;
        saved record;
        payload jsonb;
        output_valid boolean;
        new_state text;
        failure text;
        sequence bigint;
BEGIN
  IF task IS NULL OR token IS NULL OR token < 0 THEN RETURN false; END IF;
  SELECT t.id, t.owner_id, t.kind, t.state, t.lease_token, t.lease_expires_at,
      t.revision, t.class_session_id, t.not_before
    INTO candidate FROM public.tasks t WHERE t.id = task
      AND t.owner_id = nullif(current_setting('app.owner_id', true), '')::uuid FOR UPDATE;
  IF NOT FOUND OR candidate.owner_id IS DISTINCT FROM
      nullif(current_setting('app.owner_id', true), '')::uuid
      OR candidate.lease_token <> token OR candidate.state <> 'unknown'
      OR candidate.not_before > clock_timestamp()
      OR (candidate.lease_expires_at IS NOT NULL AND candidate.lease_expires_at > clock_timestamp())
      OR public.classroom_web_identity_usable(candidate.owner_id) THEN
    RETURN false;
  END IF;
  PERFORM a.id FROM public.task_attempts a
    WHERE a.owner_id = candidate.owner_id AND a.task_id = candidate.id
    ORDER BY a.attempt_number, a.id FOR UPDATE;
  -- Existing histories use the latest durable result, like _saved_result.
  -- This does not introduce a multi-Attempt workflow or settle its open P3.
  SELECT a.id AS attempt_id, a.raw_output_ref, r.id AS result_id
    INTO evidence FROM public.task_attempts a JOIN public.provider_results r
      ON r.owner_id = a.owner_id AND r.attempt_id = a.id
    WHERE a.owner_id = candidate.owner_id AND a.task_id = candidate.id
    ORDER BY a.attempt_number DESC, a.id DESC LIMIT 1;
  IF NOT FOUND OR (evidence.raw_output_ref IS NOT NULL
      AND evidence.raw_output_ref <> evidence.result_id) THEN RETURN false; END IF;
  SELECT r.id, r.owner_id, r.attempt_id, r.raw_output, r.validation_status, r.conflict_sha256
    INTO saved FROM public.provider_results r
    WHERE r.id = evidence.result_id AND r.owner_id = candidate.owner_id
      AND r.attempt_id = evidence.attempt_id FOR UPDATE;
  IF NOT FOUND OR saved.raw_output IS NULL OR saved.validation_status <> 'pending'
      OR saved.conflict_sha256 IS NOT NULL THEN RETURN false; END IF;

  payload := public.classroom_web_local_json_payload(saved.raw_output);
  output_valid := payload IS NOT NULL;
  new_state := CASE WHEN output_valid THEN 'succeeded' ELSE 'failed' END;
  failure := CASE WHEN output_valid THEN NULL ELSE 'OUTPUT_INVALID' END;
  UPDATE public.provider_results r SET
      validation_status = CASE WHEN output_valid THEN 'valid' ELSE 'invalid' END,
      validated_payload = payload
    WHERE r.id = saved.id AND r.owner_id = candidate.owner_id;
  UPDATE public.task_attempts a SET state = new_state, raw_output_ref = saved.id,
      provider_outcome = CASE WHEN a.raw_output_ref IS NULL THEN 'succeeded' ELSE a.provider_outcome END,
      error_code = failure, finished_at = coalesce(a.finished_at, clock_timestamp())
    WHERE a.id = evidence.attempt_id AND a.owner_id = candidate.owner_id
      AND a.task_id = candidate.id;
  -- No Provider call, new attempt, content publication or quota release occurs.
  -- Raw bytes/digest and provider_outcome are unchanged. Original intent and
  -- operation receipts remain byte-for-byte; this minimized event records the
  -- same task's local completion without a new public operation identity.
  UPDATE public.tasks t SET state = new_state, error_code = failure,
      may_have_charged = true, lease_token = t.lease_token + 1,
      leased_by = NULL, lease_expires_at = NULL, revision = t.revision + 1,
      updated_at = clock_timestamp(), completed_at = clock_timestamp()
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
        'state', new_state, 'error_code', failure, 'may_have_charged', true), '1');
  RETURN true;
END
$local_finish$;
REVOKE ALL ON FUNCTION classroom_web_reconcile_saved_result(uuid, bigint) FROM PUBLIC,
  classroom_web_scheduler, classroom_web_task_dispatcher, classroom_web_identity;
GRANT EXECUTE ON FUNCTION classroom_web_reconcile_saved_result(uuid, bigint)
  TO classroom_web_runtime;
