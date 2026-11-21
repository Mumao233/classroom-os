ALTER TABLE public.tasks DROP CONSTRAINT ck_tasks_schema_version;
ALTER TABLE public.tasks ADD CONSTRAINT ck_tasks_schema_version
    CHECK (schema_version IN ('1','pr07a-1','pr07b-1','pr07b-batch-1','pr08c-1'));
ALTER TABLE public.tasks DROP CONSTRAINT ck_tasks_qa_plan;
ALTER TABLE public.tasks ADD CONSTRAINT ck_tasks_qa_plan CHECK (
    (schema_version='1' AND plan_id IS NULL) OR
    (schema_version='pr07a-1' AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='ai_question') OR
    (schema_version IN ('pr07b-1','pr07b-batch-1') AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='review_summary') OR
    (schema_version='pr08c-1' AND plan_id IS NULL AND class_session_id IS NULL AND kind IN ('import','export'))
);

GRANT UPDATE (reserved_bytes) ON public.portable_transfers TO classroom_web_runtime;

CREATE TABLE public.portable_task_bindings (
    task_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    slot_id uuid NOT NULL REFERENCES public.portable_slots(id),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND pg_column_size(payload)<=4096),
    result_id uuid,
    FOREIGN KEY (owner_id,task_id) REFERENCES public.tasks(owner_id,id),
    FOREIGN KEY (owner_id,transfer_id) REFERENCES public.portable_transfers(owner_id,id),
    UNIQUE (transfer_id,slot_id)
);
CREATE UNIQUE INDEX portable_import_one_confirmation ON public.portable_task_bindings(slot_id);

CREATE TABLE public.portable_imported_classrooms (
    class_session_id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    task_id uuid NOT NULL,
    original_id text NOT NULL CHECK (original_id ~ '^[a-f0-9]{32}$'),
    original_sha256 text NOT NULL CHECK (original_sha256 ~ '^[a-f0-9]{64}$'),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot)='object' AND pg_column_size(snapshot)<=32000000),
    id_map jsonb NOT NULL CHECK (jsonb_typeof(id_map)='object' AND pg_column_size(id_map)<=8000000),
    FOREIGN KEY (owner_id,task_id) REFERENCES public.tasks(owner_id,id),
    FOREIGN KEY (owner_id,class_session_id) REFERENCES public.class_sessions(owner_id,id),
    UNIQUE (task_id,original_id)
);

CREATE TABLE public.portable_import_assets (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    task_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    classroom_alias text NOT NULL CHECK (classroom_alias ~ '^[a-f0-9]{32}$'),
    archive_path text NOT NULL CHECK (archive_path ~ '^assets/[a-f0-9]{64}[.](png|jpg)$'),
    sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    byte_length bigint NOT NULL CHECK (byte_length BETWEEN 1 AND 16000000),
    object_key text NOT NULL UNIQUE CHECK (object_key ~ '^media/[a-f0-9]{32}/[a-f0-9]{32}/[a-f0-9]{64}$'),
    published_at timestamptz,
    erased_at timestamptz,
    CHECK (published_at IS NULL OR erased_at IS NULL),
    FOREIGN KEY (owner_id,task_id) REFERENCES public.tasks(owner_id,id),
    FOREIGN KEY (owner_id,transfer_id) REFERENCES public.portable_transfers(owner_id,id),
    UNIQUE (task_id,classroom_alias,archive_path)
);
CREATE INDEX portable_import_assets_pending ON public.portable_import_assets(transfer_id)
    WHERE published_at IS NULL AND erased_at IS NULL;
ALTER TABLE public.portable_slots ADD COLUMN recover_until timestamptz;
ALTER TABLE public.portable_slots ADD COLUMN recovery_count integer NOT NULL DEFAULT 0 CHECK (recovery_count BETWEEN 0 AND 1);
GRANT UPDATE (recover_until,recovery_count) ON public.portable_slots TO classroom_web_runtime;
GRANT UPDATE (recover_until) ON public.portable_slots TO classroom_web_portable_maintainer;
CREATE POLICY portable_recovery_slot ON public.portable_task_bindings FOR SELECT
    TO classroom_web_portable_maintainer USING (true);
GRANT SELECT (slot_id) ON public.portable_task_bindings TO classroom_web_portable_maintainer;
ALTER TABLE public.portable_slots ADD CONSTRAINT portable_slots_owner_identity UNIQUE(owner_id,id,transfer_id);
ALTER TABLE public.portable_task_bindings ADD FOREIGN KEY (owner_id,slot_id,transfer_id)
    REFERENCES public.portable_slots(owner_id,id,transfer_id);

CREATE TABLE public.portable_export_dependencies (
    owner_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('media','transcript')),
    source_id uuid NOT NULL,
    PRIMARY KEY (transfer_id,kind,source_id),
    FOREIGN KEY (owner_id,transfer_id) REFERENCES public.portable_transfers(owner_id,id)
);
CREATE INDEX portable_export_dependency_source ON public.portable_export_dependencies(kind,source_id);

DO $policies$
DECLARE n text;
BEGIN
    FOREACH n IN ARRAY ARRAY['portable_task_bindings','portable_imported_classrooms','portable_import_assets','portable_export_dependencies'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',n);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',n);
        EXECUTE format('CREATE POLICY portable_owner ON public.%I TO classroom_web_runtime
            USING (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
              AND public.classroom_web_owner_active())
            WITH CHECK (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
              AND public.classroom_web_owner_active())', n);
        EXECUTE format('GRANT SELECT,INSERT ON public.%I TO classroom_web_runtime',n);
    END LOOP;
END
$policies$;
GRANT UPDATE (result_id) ON public.portable_task_bindings TO classroom_web_runtime;
GRANT UPDATE (published_at) ON public.portable_import_assets TO classroom_web_runtime;
CREATE POLICY portable_maintainer ON public.portable_import_assets TO classroom_web_portable_maintainer
    USING (true) WITH CHECK (true);
GRANT SELECT, UPDATE (erased_at) ON public.portable_import_assets TO classroom_web_portable_maintainer;

CREATE POLICY portable_maintainer ON public.portable_export_dependencies
    FOR SELECT TO classroom_web_portable_maintainer USING (true);
GRANT SELECT ON public.portable_export_dependencies TO classroom_web_portable_maintainer;
GRANT SELECT (access_revoked_at) ON public.media_assets TO classroom_web_portable_maintainer;
CREATE POLICY portable_export_media_read ON public.media_assets FOR SELECT TO classroom_web_portable_maintainer
    USING (EXISTS (SELECT FROM public.portable_export_dependencies d
        WHERE d.owner_id=media_assets.owner_id AND d.kind='media' AND d.source_id=media_assets.id));
GRANT SELECT (id,owner_id,revoked_at) ON public.qa_sources TO classroom_web_portable_maintainer;
CREATE POLICY portable_export_transcript_read ON public.qa_sources FOR SELECT TO classroom_web_portable_maintainer
    USING (EXISTS (SELECT FROM public.portable_export_dependencies d
        WHERE d.owner_id=qa_sources.owner_id AND d.kind='transcript' AND d.source_id=qa_sources.id));

CREATE POLICY portable_import_room_live ON public.portable_imported_classrooms AS RESTRICTIVE
    TO classroom_web_runtime USING (public.classroom_web_qa_class_live(class_session_id))
    WITH CHECK (public.classroom_web_qa_class_live(class_session_id));

GRANT SELECT (id,owner_id,asset_id,state), UPDATE (state)
    ON public.media_uploads TO classroom_web_portable_maintainer;
CREATE POLICY portable_legacy_upload_keeper ON public.media_uploads TO classroom_web_portable_maintainer
    USING (EXISTS(SELECT FROM public.portable_transfer_media p
        WHERE (p.owner_id,p.asset_id)=(media_uploads.owner_id,media_uploads.asset_id)))
    WITH CHECK (EXISTS(SELECT FROM public.portable_transfer_media p
        WHERE (p.owner_id,p.asset_id)=(media_uploads.owner_id,media_uploads.asset_id)));

CREATE FUNCTION public.classroom_web_portable_import_cleanup(batch integer, settle integer)
RETURNS TABLE(id uuid, object_key text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $cleanup$
    SELECT a.id,a.object_key FROM public.portable_import_assets a
      JOIN public.portable_transfers t ON (t.owner_id,t.id)=(a.owner_id,a.transfer_id)
     WHERE t.state='closed' AND t.access_until<=clock_timestamp()-make_interval(secs=>greatest(120,settle))
       AND a.published_at IS NULL AND a.erased_at IS NULL
     ORDER BY t.access_until,a.id LIMIT least(100,greatest(0,batch));
$cleanup$;

CREATE FUNCTION public.classroom_web_portable_import_lock(asset uuid, settle integer)
RETURNS text LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $lock$
    SELECT a.object_key FROM public.portable_import_assets a
        JOIN public.portable_transfers t ON (t.owner_id,t.id)=(a.owner_id,a.transfer_id)
    WHERE a.id=asset AND t.state='closed'
        AND t.access_until<=clock_timestamp()-make_interval(secs=>greatest(120,settle))
        AND a.published_at IS NULL AND a.erased_at IS NULL
    FOR UPDATE OF a SKIP LOCKED;
$lock$;
CREATE FUNCTION public.classroom_web_portable_import_erased(asset uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $erase$
    WITH removed AS (
        UPDATE public.portable_import_assets a SET erased_at=clock_timestamp()
          FROM public.portable_transfers t
         WHERE a.id=asset AND (t.owner_id,t.id)=(a.owner_id,a.transfer_id)
           AND t.state='closed' AND a.published_at IS NULL AND a.erased_at IS NULL
        RETURNING a.id
    ) SELECT EXISTS(SELECT FROM removed);
$erase$;
GRANT CREATE ON SCHEMA public TO classroom_web_portable_maintainer;
ALTER FUNCTION public.classroom_web_portable_import_cleanup(integer,integer) OWNER TO classroom_web_portable_maintainer;
ALTER FUNCTION public.classroom_web_portable_import_lock(uuid,integer) OWNER TO classroom_web_portable_maintainer;
ALTER FUNCTION public.classroom_web_portable_import_erased(uuid) OWNER TO classroom_web_portable_maintainer;
REVOKE CREATE ON SCHEMA public FROM classroom_web_portable_maintainer;
REVOKE ALL ON FUNCTION public.classroom_web_portable_import_cleanup(integer,integer),
    public.classroom_web_portable_import_erased(uuid),public.classroom_web_portable_import_lock(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_portable_import_cleanup(integer,integer),
    public.classroom_web_portable_import_erased(uuid),public.classroom_web_portable_import_lock(uuid,integer)
    TO classroom_web_deletion_worker;

CREATE OR REPLACE FUNCTION public.classroom_web_portable_gate()
RETURNS TABLE(slots bigint, import_bytes bigint, export_bytes bigint, alerts bigint, incidents bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $gate$
DECLARE ts timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(41006,21);
    ts := clock_timestamp();

    WITH expired AS (
        UPDATE public.portable_slots s
        SET state=CASE WHEN s.state='running' THEN 'failed' ELSE 'expired' END,
            finished_at=s.deadline, worker_id=NULL, fence=s.fence+1,
            recover_until=CASE WHEN s.state='running' AND s.recovery_count=0
                AND EXISTS(SELECT FROM public.portable_task_bindings b WHERE b.slot_id=s.id)
                THEN s.deadline+interval '5 minutes' END
        WHERE s.state IN ('running','waiting') AND s.deadline<=ts
        RETURNING s.transfer_id, s.state, s.finished_at, s.recover_until
    )
    UPDATE public.portable_transfers t SET state='closed',
        access_until=least(t.access_until, e.finished_at)
    FROM expired e WHERE e.transfer_id=t.id AND e.state='failed' AND t.state<>'closed'
        AND (e.recover_until IS NULL OR e.recover_until<=ts);

    UPDATE public.portable_transfers t SET state='closed',access_until=least(t.access_until,s.finished_at)
        FROM public.portable_slots s WHERE s.transfer_id=t.id AND s.state='failed' AND s.recover_until<=ts
        AND t.state<>'closed';

    WITH cutoffs AS (
        SELECT t.id, min(r.access_revoked_at) AS deadline
        FROM public.portable_transfers t
        LEFT JOIN public.portable_export_sources s ON (s.owner_id,s.transfer_id)=(t.owner_id,t.id)
        LEFT JOIN public.class_sessions c ON (c.owner_id,c.id)=(s.owner_id,s.class_session_id)
        LEFT JOIN public.courses p ON p.owner_id=t.owner_id AND p.id=coalesce(c.course_id,t.destination_course_id)
        JOIN public.deletion_tombstones d ON d.owner_id=t.owner_id AND (
            (d.resource_type='account' AND d.resource_id=t.owner_id)
            OR (d.resource_type='class_session' AND d.resource_id=c.id)
            OR (d.resource_type='course' AND d.resource_id=p.id)
            OR (d.resource_type='semester' AND d.resource_id=p.semester_id))
        JOIN public.deletion_requests r ON (r.owner_id,r.id)=(d.owner_id,d.request_id)
        WHERE t.erased_at IS NULL
        GROUP BY t.id
    )
    UPDATE public.portable_transfers t SET state='closed', access_until=least(t.access_until,c.deadline)
    FROM cutoffs c WHERE t.id=c.id AND (t.state<>'closed' OR t.access_until>c.deadline);

    WITH revoked AS (
        SELECT d.transfer_id,min(coalesce(a.access_revoked_at,q.revoked_at)) AS deadline
        FROM public.portable_export_dependencies d JOIN public.portable_transfers t ON t.id=d.transfer_id
        LEFT JOIN public.media_assets a ON d.kind='media' AND (a.owner_id,a.id)=(d.owner_id,d.source_id)
        LEFT JOIN public.qa_sources q ON d.kind='transcript' AND (q.owner_id,q.id)=(d.owner_id,d.source_id)
        WHERE t.erased_at IS NULL AND (a.access_revoked_at IS NOT NULL OR q.revoked_at IS NOT NULL)
        GROUP BY d.transfer_id
    )
    UPDATE public.portable_transfers t SET state='closed',access_until=least(t.access_until,r.deadline)
        FROM revoked r WHERE t.id=r.transfer_id AND (t.state<>'closed' OR t.access_until>r.deadline);

    UPDATE public.portable_transfers t SET state='closed'
    WHERE t.state<>'closed' AND t.access_until<=ts;
    UPDATE public.portable_slots s SET state='cancelled', worker_id=NULL, fence=s.fence+1,
        finished_at=t.access_until
    FROM public.portable_transfers t
    WHERE t.id=s.transfer_id AND t.state='closed' AND s.state IN ('running','waiting');

    WITH due AS (
        SELECT a.id,t.access_until FROM public.media_assets a
        JOIN public.portable_transfer_media l ON (l.owner_id,l.asset_id)=(a.owner_id,a.id)
        JOIN public.portable_transfers t ON (t.owner_id,t.id)=(l.owner_id,l.transfer_id)
        WHERE t.state='closed' AND a.status IN ('staging','verified','committed')
            AND (a.expires_at IS NULL OR a.expires_at>t.access_until)
        ORDER BY a.id FOR UPDATE OF a SKIP LOCKED
    )
    UPDATE public.media_assets a SET expires_at=due.access_until FROM due WHERE a.id=due.id;

    WITH retired AS (
        SELECT u.id FROM public.media_uploads u
        JOIN public.portable_transfer_media l ON (l.owner_id,l.asset_id)=(u.owner_id,u.asset_id)
        JOIN public.portable_transfers t ON (t.owner_id,t.id)=(l.owner_id,l.transfer_id)
        WHERE t.kind='import' AND t.state='closed' AND u.state IN ('staging','uploaded')
        ORDER BY u.id FOR UPDATE OF u SKIP LOCKED
    )
    UPDATE public.media_uploads u SET state='rejected' FROM retired WHERE u.id=retired.id;

    UPDATE public.portable_transfers t SET erased_at=ts
    WHERE t.state='closed' AND t.erased_at IS NULL AND NOT EXISTS (
        SELECT FROM public.portable_transfer_media l JOIN public.media_assets a
            ON (a.owner_id,a.id)=(l.owner_id,l.asset_id)
        WHERE (l.owner_id,l.transfer_id)=(t.owner_id,t.id) AND a.status<>'deleted'
    ) AND NOT EXISTS (
        SELECT FROM public.portable_import_assets a WHERE (a.owner_id,a.transfer_id)=(t.owner_id,t.id)
            AND a.published_at IS NULL AND a.erased_at IS NULL
    );

    RETURN QUERY SELECT
        (SELECT count(*) FROM public.portable_slots s WHERE s.state IN ('running','waiting')),
        coalesce(sum(t.reserved_bytes) FILTER (WHERE t.kind='import'),0)::bigint
          + coalesce((SELECT sum(a.byte_length) FROM public.portable_import_assets a
                     WHERE a.published_at IS NULL AND a.erased_at IS NULL),0)::bigint,
        coalesce(sum(t.reserved_bytes) FILTER (WHERE t.kind='export'),0)::bigint,
        count(*) FILTER (WHERE t.state='closed' AND t.access_until<=ts-interval '24 hours'),
        count(*) FILTER (WHERE t.state='closed' AND t.access_until<=ts-interval '72 hours')
    FROM public.portable_transfers t WHERE t.erased_at IS NULL;
END
$gate$;

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
      OR requested_schema IS NULL OR requested_schema NOT IN ('1','pr07a-1','pr07b-1','pr07b-batch-1','pr08c-1') THEN
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
