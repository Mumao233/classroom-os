ALTER TABLE public.generation_plans ALTER COLUMN snapshot_id DROP NOT NULL;
ALTER TABLE public.generation_plans ADD COLUMN audio_manifest jsonb,
    ADD COLUMN audio_manifest_sha256 char(64),
    DROP CONSTRAINT generation_plans_kind_check,
    ADD CONSTRAINT generation_plans_kind_check CHECK(kind IN ('ai_question','review_summary','asr_retranscribe')),
    ADD CONSTRAINT filetrans_plan_source CHECK (
      (kind='asr_retranscribe' AND snapshot_id IS NULL AND audio_manifest IS NOT NULL
       AND jsonb_typeof(audio_manifest)='object' AND pg_column_size(audio_manifest)<=32000000
       AND audio_manifest_sha256 IS NOT NULL AND audio_manifest_sha256 ~ '^[a-f0-9]{64}$')
      OR (kind<>'asr_retranscribe' AND snapshot_id IS NOT NULL AND audio_manifest IS NULL AND audio_manifest_sha256 IS NULL)),
    ADD CONSTRAINT generation_class_fk FOREIGN KEY(owner_id,class_session_id) REFERENCES public.class_sessions(owner_id,id);

ALTER POLICY qa_plans_owner ON public.generation_plans
USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND public.classroom_web_owner_active()
    AND CASE WHEN kind='asr_retranscribe' THEN public.classroom_web_qa_class_live(class_session_id)
             ELSE EXISTS(SELECT FROM public.source_snapshots s WHERE s.owner_id=generation_plans.owner_id AND s.id=snapshot_id) END);

ALTER TABLE public.tasks DROP CONSTRAINT ck_tasks_schema_version, DROP CONSTRAINT ck_tasks_qa_plan;
ALTER TABLE public.tasks ADD CONSTRAINT ck_tasks_schema_version
    CHECK(schema_version IN ('1','pr07a-1','pr07b-1','pr07b-batch-1','pr08c-1','pr06b-1','pr06c-1')),
    ADD CONSTRAINT ck_tasks_qa_plan CHECK (
      (schema_version='1' AND plan_id IS NULL) OR
      (schema_version='pr07a-1' AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='ai_question') OR
      (schema_version IN ('pr07b-1','pr07b-batch-1') AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='review_summary') OR
      (schema_version='pr08c-1' AND plan_id IS NULL AND class_session_id IS NULL AND kind IN ('import','export')) OR
      (schema_version='pr06b-1' AND plan_id IS NULL AND class_session_id IS NOT NULL AND kind='asr_live') OR
      (schema_version='pr06c-1' AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='asr_retranscribe'));
ALTER TABLE public.tasks ADD CONSTRAINT filetrans_task_class_key UNIQUE(owner_id,class_session_id,id);
CREATE OR REPLACE FUNCTION public.classroom_web_guard_qa_plan() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF (to_jsonb(NEW)-ARRAY['consumed_task_id','consumed_at'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['consumed_task_id','consumed_at'])
    OR OLD.consumed_task_id IS NOT NULL OR NEW.consumed_task_id IS NULL THEN
   RAISE EXCEPTION 'generation plan can be consumed once' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT FROM tasks t WHERE t.owner_id=NEW.owner_id AND t.id=NEW.consumed_task_id
    AND t.plan_id=NEW.id AND t.class_session_id=NEW.class_session_id AND t.kind=NEW.kind
    AND ((NEW.kind='ai_question' AND t.schema_version='pr07a-1') OR
         (NEW.kind='review_summary' AND t.schema_version IN ('pr07b-1','pr07b-batch-1')) OR
         (NEW.kind='asr_retranscribe' AND t.schema_version='pr06c-1' AND t.parent_task_id IS NULL))) THEN
   RAISE EXCEPTION 'consumed task must belong to this plan and classroom' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;

CREATE TABLE public.filetrans_approvals (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    class_session_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    original_task_id uuid,
    actor_id uuid NOT NULL REFERENCES public.users(id),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 300),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+interval '24 hours'),
    UNIQUE(owner_id,class_session_id,plan_id,id),
    FOREIGN KEY(owner_id,plan_id,class_session_id) REFERENCES public.generation_plans(owner_id,id,class_session_id),
    FOREIGN KEY(owner_id,class_session_id,original_task_id) REFERENCES public.tasks(owner_id,class_session_id,id)
);

CREATE TABLE public.filetrans_runs (
    task_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    class_session_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    auth_session_id uuid NOT NULL,
    device_id uuid NOT NULL,
    security_epoch bigint NOT NULL CHECK(security_epoch>0),
    auth_role text NOT NULL CHECK(auth_role IN ('student','admin')),
    approval_id uuid UNIQUE,
    original_task_id uuid UNIQUE,
    original_attempt_id uuid,
    UNIQUE(owner_id,class_session_id,task_id),
    FOREIGN KEY(owner_id,class_session_id,task_id) REFERENCES public.tasks(owner_id,class_session_id,id),
    FOREIGN KEY(owner_id,plan_id,class_session_id) REFERENCES public.generation_plans(owner_id,id,class_session_id),
    FOREIGN KEY(owner_id,auth_session_id) REFERENCES public.auth_sessions(owner_id,id),
    FOREIGN KEY(owner_id,device_id) REFERENCES public.devices(owner_id,id),
    FOREIGN KEY(owner_id,class_session_id,plan_id,approval_id)
        REFERENCES public.filetrans_approvals(owner_id,class_session_id,plan_id,id),
    FOREIGN KEY(owner_id,class_session_id,original_task_id) REFERENCES public.filetrans_runs(owner_id,class_session_id,task_id),
    FOREIGN KEY(owner_id,original_task_id,original_attempt_id) REFERENCES public.task_attempts(owner_id,task_id,id),
    CHECK ((original_task_id IS NULL)=(original_attempt_id IS NULL)),
    CHECK (original_task_id IS NULL OR approval_id IS NOT NULL)
);
CREATE UNIQUE INDEX filetrans_first_per_class ON public.filetrans_runs(owner_id,class_session_id) WHERE approval_id IS NULL;

CREATE TABLE public.filetrans_versions (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    class_session_id uuid NOT NULL,
    task_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(owner_id,id,class_session_id),
    FOREIGN KEY(owner_id,class_session_id,task_id) REFERENCES public.filetrans_runs(owner_id,class_session_id,task_id)
);
CREATE INDEX filetrans_versions_class ON public.filetrans_versions(owner_id,class_session_id,id);

CREATE TABLE public.filetrans_segments (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    class_session_id uuid NOT NULL,
    version_id uuid NOT NULL,
    segment_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    source_chunk_ids uuid[] NOT NULL CHECK(cardinality(source_chunk_ids) BETWEEN 1 AND 1000),
    start_ms bigint NOT NULL CHECK(start_ms>=0),
    end_ms bigint NOT NULL CHECK(end_ms>start_ms),
    body text NOT NULL CHECK(length(body) BETWEEN 1 AND 20000),
    provider_request_id text NOT NULL CHECK(length(provider_request_id) BETWEEN 1 AND 200),
    FOREIGN KEY(owner_id,version_id,class_session_id) REFERENCES public.filetrans_versions(owner_id,id,class_session_id)
);
CREATE INDEX filetrans_segments_version ON public.filetrans_segments(owner_id,version_id,id);

DO $ddl$
DECLARE name text;
BEGIN
  FOREACH name IN ARRAY ARRAY['filetrans_approvals','filetrans_runs','filetrans_versions','filetrans_segments'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',name);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',name);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC',name);
    EXECUTE format('CREATE POLICY filetrans_owner ON public.%I TO classroom_web_runtime
      USING (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
        AND public.classroom_web_owner_active() AND public.classroom_web_qa_class_live(class_session_id))',name);
    EXECUTE format('GRANT SELECT ON public.%I TO classroom_web_runtime',name);
  END LOOP;
END
$ddl$;
GRANT INSERT ON public.filetrans_runs,public.filetrans_versions,public.filetrans_segments TO classroom_web_runtime;

CREATE FUNCTION public.classroom_web_approve_filetrans(p_id uuid,p_actor uuid,p_owner uuid,p_class uuid,
    p_plan uuid,p_original uuid,p_reason text,p_expires timestamptz) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $approval$
BEGIN
    IF NOT EXISTS(SELECT FROM public.users WHERE id=p_actor AND state='active' AND role='admin')
       OR NOT EXISTS(SELECT FROM public.generation_plans WHERE (owner_id,class_session_id,id,kind)=
                        (p_owner,p_class,p_plan,'asr_retranscribe'))
       OR NOT EXISTS(SELECT FROM public.class_sessions WHERE owner_id=p_owner AND id=p_class AND deleted_at IS NULL) THEN
        RAISE EXCEPTION 'filetrans approval scope unavailable' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.filetrans_approvals(id,owner_id,class_session_id,plan_id,original_task_id,actor_id,reason,expires_at)
        VALUES(p_id,p_owner,p_class,p_plan,p_original,p_actor,p_reason,p_expires);
    RETURN p_id;
END
$approval$;
REVOKE ALL ON FUNCTION public.classroom_web_approve_filetrans(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz) FROM PUBLIC;

ALTER TABLE public.class_sessions DROP CONSTRAINT ck_class_sessions_transcript_not_implemented;
CREATE FUNCTION public.classroom_web_guard_transcript_pointer() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $pointer$
BEGIN
    IF NEW.adopted_transcript_version_id IS NOT NULL
       AND NOT EXISTS (SELECT FROM public.filetrans_versions v WHERE (v.owner_id,v.class_session_id,v.id)=
                         (NEW.owner_id,NEW.id,NEW.adopted_transcript_version_id))
       AND NOT EXISTS (SELECT FROM public.asr_streams s WHERE (s.owner_id,s.class_session_id,s.version_id)=
                         (NEW.owner_id,NEW.id,NEW.adopted_transcript_version_id)
                         AND s.state IN ('succeeded','failed','unknown')
                         AND EXISTS(SELECT FROM public.asr_final_segments f WHERE (f.owner_id,f.stream_id)=(s.owner_id,s.id))) THEN
        RAISE EXCEPTION 'transcript version must be sealed in its classroom' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$pointer$;
REVOKE ALL ON FUNCTION public.classroom_web_guard_transcript_pointer() FROM PUBLIC;
CREATE TRIGGER filetrans_adopt_lineage BEFORE UPDATE OF adopted_transcript_version_id ON public.class_sessions
FOR EACH ROW WHEN (OLD.adopted_transcript_version_id IS DISTINCT FROM NEW.adopted_transcript_version_id)
EXECUTE FUNCTION public.classroom_web_guard_transcript_pointer();

ALTER TABLE public.qa_sources DROP CONSTRAINT qa_sources_origin_check;
ALTER TABLE public.qa_sources ADD CONSTRAINT qa_sources_origin_check
    CHECK(origin IN ('manual','material','live_asr','import','retranscription'));
CREATE FUNCTION public.classroom_web_current_transcript(p_owner uuid,p_class uuid,p_origin text,
    p_source uuid,p_adopted uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=public,pg_temp AS $filter$
    SELECT CASE
      WHEN p_origin='retranscription' THEN EXISTS(SELECT FROM public.filetrans_segments f
        WHERE (f.owner_id,f.class_session_id,f.version_id,f.segment_id)=(p_owner,p_class,p_adopted,p_source))
      WHEN p_origin='live_asr' AND p_adopted IS NOT NULL THEN EXISTS(SELECT FROM public.asr_final_segments f
        JOIN public.asr_streams s ON (s.owner_id,s.id)=(f.owner_id,f.stream_id)
        WHERE (s.owner_id,s.class_session_id,s.version_id,f.segment_id)=(p_owner,p_class,p_adopted,p_source))
      ELSE true
    END
$filter$;
REVOKE ALL ON FUNCTION public.classroom_web_current_transcript(uuid,uuid,text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_current_transcript(uuid,uuid,text,uuid,uuid) TO classroom_web_runtime;

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
      OR requested_schema IS NULL OR requested_schema NOT IN ('1','pr07a-1','pr07b-1','pr07b-batch-1','pr08c-1','pr06b-1','pr06c-1') THEN
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
