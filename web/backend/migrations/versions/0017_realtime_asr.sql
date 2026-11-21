ALTER TABLE public.tasks DROP CONSTRAINT ck_tasks_schema_version;
ALTER TABLE public.tasks ADD CONSTRAINT ck_tasks_schema_version
    CHECK (schema_version IN ('1','pr07a-1','pr07b-1','pr07b-batch-1','pr08c-1','pr06b-1'));
ALTER TABLE public.tasks DROP CONSTRAINT ck_tasks_qa_plan;
ALTER TABLE public.tasks ADD CONSTRAINT ck_tasks_qa_plan CHECK (
    (schema_version='1' AND plan_id IS NULL) OR
    (schema_version='pr07a-1' AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='ai_question') OR
    (schema_version IN ('pr07b-1','pr07b-batch-1') AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='review_summary') OR
    (schema_version='pr08c-1' AND plan_id IS NULL AND class_session_id IS NULL AND kind IN ('import','export')) OR
    (schema_version='pr06b-1' AND plan_id IS NULL AND class_session_id IS NOT NULL AND kind='asr_live')
);

ALTER TABLE public.task_attempts ADD CONSTRAINT asr_attempt_exact_task UNIQUE(owner_id,task_id,id);

CREATE TABLE public.asr_streams (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    class_session_id uuid NOT NULL,
    capture_epoch_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid,
    originating_attempt_id uuid,
    provider_task_id uuid NOT NULL UNIQUE,
    provider_request_id text CHECK (length(provider_request_id) BETWEEN 1 AND 200),
    version_id uuid NOT NULL UNIQUE,
    auth_session_id uuid NOT NULL,
    device_id uuid NOT NULL,
    security_epoch bigint NOT NULL CHECK (security_epoch > 0),
    auth_role text NOT NULL CHECK (auth_role IN ('student','admin')),
    capture_fence bigint NOT NULL CHECK (capture_fence > 0),
    consent_version text NOT NULL CHECK (length(consent_version) BETWEEN 1 AND 64),
    mode text NOT NULL CHECK (mode IN ('live','catch_up')),
    source_scope jsonb NOT NULL CHECK (jsonb_typeof(source_scope)='array' AND pg_column_size(source_scope)<=65536),
    state text NOT NULL DEFAULT 'idle'
        CHECK (state IN ('idle','streaming','catching_up','draining','succeeded','failed','unknown')),
    requested_finish_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id,id), UNIQUE (owner_id,task_id), UNIQUE (owner_id,originating_attempt_id),
    FOREIGN KEY (owner_id,class_session_id) REFERENCES public.class_sessions(owner_id,id),
    FOREIGN KEY (owner_id,capture_epoch_id) REFERENCES public.capture_epochs(owner_id,id),
    FOREIGN KEY (owner_id,task_id) REFERENCES public.tasks(owner_id,id),
    FOREIGN KEY (owner_id,task_id,attempt_id) REFERENCES public.task_attempts(owner_id,task_id,id),
    FOREIGN KEY (owner_id,auth_session_id) REFERENCES public.auth_sessions(owner_id,id),
    FOREIGN KEY (owner_id,device_id) REFERENCES public.devices(owner_id,id)
);
CREATE UNIQUE INDEX asr_one_active_per_epoch ON public.asr_streams(owner_id,capture_epoch_id)
    WHERE state IN ('idle','streaming','catching_up','draining');

CREATE TABLE public.asr_dispatches (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    stream_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    capture_epoch_id uuid NOT NULL,
    start_sample bigint NOT NULL CHECK (start_sample>=0),
    end_sample bigint NOT NULL CHECK (end_sample>start_sample),
    frame_sha256 char(64) NOT NULL CHECK (frame_sha256 ~ '^[a-f0-9]{64}$'),
    handed_off boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id,stream_id,start_sample,end_sample),
    FOREIGN KEY (owner_id,stream_id) REFERENCES public.asr_streams(owner_id,id),
    FOREIGN KEY (owner_id,chunk_id) REFERENCES public.audio_chunks(owner_id,id),
    FOREIGN KEY (owner_id,capture_epoch_id) REFERENCES public.capture_epochs(owner_id,id)
);
CREATE INDEX asr_dispatch_epoch_ranges
    ON public.asr_dispatches(owner_id,capture_epoch_id,start_sample,end_sample);

CREATE TABLE public.asr_final_segments (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    stream_id uuid NOT NULL,
    segment_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision>=1),
    sequence bigint NOT NULL CHECK (sequence>=0),
    start_ms bigint NOT NULL CHECK (start_ms>=0),
    end_ms bigint NOT NULL CHECK (end_ms>=start_ms),
    end_sample bigint NOT NULL CHECK (end_sample>0),
    source_chunk_ids uuid[] NOT NULL CHECK (cardinality(source_chunk_ids) BETWEEN 1 AND 1000),
    body text NOT NULL CHECK (char_length(body)<=20000),
    provider_request_id text CHECK (length(provider_request_id) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id,stream_id,segment_id),
    FOREIGN KEY (owner_id,stream_id) REFERENCES public.asr_streams(owner_id,id)
);

DO $asr$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['asr_streams','asr_dispatches','asr_final_segments'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', name);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', name);
        EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC', name);
        EXECUTE format('CREATE POLICY asr_owner ON public.%I TO classroom_web_runtime
           USING (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
               AND public.classroom_web_owner_active())
           WITH CHECK (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
               AND public.classroom_web_owner_active())', name);
        EXECUTE format('GRANT SELECT, INSERT ON public.%I TO classroom_web_runtime', name);
    END LOOP;
END
$asr$;
CREATE POLICY asr_stream_live ON public.asr_streams AS RESTRICTIVE
    TO classroom_web_runtime USING (public.classroom_web_qa_class_live(class_session_id))
    WITH CHECK (public.classroom_web_qa_class_live(class_session_id));
CREATE POLICY asr_visible_dispatch ON public.asr_dispatches AS RESTRICTIVE
    TO classroom_web_runtime USING (EXISTS(SELECT FROM public.asr_streams s
        WHERE (s.owner_id,s.id)=(asr_dispatches.owner_id,asr_dispatches.stream_id)))
    WITH CHECK (EXISTS(SELECT FROM public.asr_streams s
        WHERE (s.owner_id,s.id)=(asr_dispatches.owner_id,asr_dispatches.stream_id)));
CREATE POLICY asr_visible_final ON public.asr_final_segments AS RESTRICTIVE
    TO classroom_web_runtime USING (EXISTS(SELECT FROM public.asr_streams s
        WHERE (s.owner_id,s.id)=(asr_final_segments.owner_id,asr_final_segments.stream_id)))
    WITH CHECK (EXISTS(SELECT FROM public.asr_streams s
        WHERE (s.owner_id,s.id)=(asr_final_segments.owner_id,asr_final_segments.stream_id)));
GRANT UPDATE (state,attempt_id,provider_request_id,requested_finish_at,updated_at)
    ON public.asr_streams TO classroom_web_runtime;
GRANT UPDATE (handed_off) ON public.asr_dispatches TO classroom_web_runtime;

-- Both the specialized worker and the existing revocation/lease recovery can
-- close an attempt. Synchronize that durable fact in the same transaction.
CREATE FUNCTION public.classroom_web_asr_terminal() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $asr_terminal$
BEGIN
    UPDATE public.asr_streams SET state=CASE WHEN NEW.state='unknown' THEN 'unknown' ELSE 'failed' END,
        updated_at=clock_timestamp()
    WHERE owner_id=NEW.owner_id AND task_id=NEW.id AND state IN ('idle','streaming','catching_up','draining');
    RETURN NEW;
END;
$asr_terminal$;
REVOKE ALL ON FUNCTION public.classroom_web_asr_terminal() FROM PUBLIC;
CREATE TRIGGER asr_task_terminal AFTER UPDATE OF state ON public.tasks
FOR EACH ROW WHEN (NEW.schema_version='pr06b-1' AND NEW.state IN ('unknown','cancelled','failed'))
EXECUTE FUNCTION public.classroom_web_asr_terminal();

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
      OR requested_schema IS NULL OR requested_schema NOT IN ('1','pr07a-1','pr07b-1','pr07b-batch-1','pr08c-1','pr06b-1') THEN
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
