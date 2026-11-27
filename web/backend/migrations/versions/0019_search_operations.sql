-- Index authority rows directly. Review/transcript contents are evaluated only
-- through the live pointers, so raw, historical and revoked copies need no search cleanup.
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE INDEX pr11c_course_name_trgm ON public.courses USING gin (lower(name) public.gin_trgm_ops)
    WHERE deleted_at IS NULL;
CREATE INDEX pr11c_class_title_trgm ON public.class_sessions USING gin (lower(title) public.gin_trgm_ops)
    WHERE deleted_at IS NULL;

ALTER TABLE operation_receipts DROP CONSTRAINT ck_operation_receipts_resource_type;
ALTER TABLE operation_receipts ADD CONSTRAINT ck_operation_receipts_resource_type
 CHECK(resource_type IN ('task','semester','course','class_session','course_material_version',
  'media_asset','media_upload','generation_plan','question','review_document','review_revision',
  'review_conflict','deletion_request','mark','sync_operation','sync_batch','notification','feedback_report'));
CREATE INDEX pr11c_feedback_rate ON operation_receipts(owner_id,created_at)
    WHERE operation='submitFeedback';

DO $roles$
DECLARE candidate text;
BEGIN
  FOREACH candidate IN ARRAY ARRAY['classroom_web_notification_writer','classroom_web_feedback_projection',
      'classroom_web_feedback_admin','classroom_web_feedback_excerpt'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=candidate) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',candidate);
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname=candidate AND
      (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin OR rolreplication OR rolinherit))
      OR EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=candidate) THEN
      RAISE EXCEPTION 'PR-11c roles must be unprivileged, independent NOLOGIN groups';
    END IF;
  END LOOP;
  IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
      WHERE r.rolname IN ('classroom_web_notification_writer','classroom_web_feedback_projection')) THEN
    RAISE EXCEPTION 'PR-11c callable owners must have no members';
  END IF;
  IF EXISTS (
    WITH RECURSIVE membership(root,member) AS (
      SELECT r.rolname,r.oid FROM pg_roles r WHERE r.rolname IN
        ('classroom_web_runtime','classroom_web_feedback_admin','classroom_web_feedback_excerpt')
      UNION
      SELECT p.root,m.member FROM membership p JOIN pg_auth_members m ON m.roleid=p.member
    )
    SELECT FROM membership runtime JOIN membership reader USING (member)
    WHERE runtime.root='classroom_web_runtime' AND reader.root<>'classroom_web_runtime'
  ) THEN
    RAISE EXCEPTION 'Web runtime and feedback reader memberships must be disjoint';
  END IF;
END
$roles$;
GRANT USAGE ON SCHEMA public TO classroom_web_notification_writer, classroom_web_feedback_projection,
    classroom_web_feedback_admin, classroom_web_feedback_excerpt;
REVOKE CREATE ON SCHEMA public FROM classroom_web_notification_writer, classroom_web_feedback_projection,
    classroom_web_feedback_admin, classroom_web_feedback_excerpt;

CREATE TABLE public.notifications (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES public.users(id),
    object_id uuid,
    class_session_id uuid,
    source_conflict_id uuid,
    kind text NOT NULL CHECK (kind IN ('task_completed','conflict','audio_expiry','capture_failure','save_failure','security')),
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    message_code text NOT NULL CHECK (message_code ~ '^[A-Z0-9_]{1,64}$'),
    dedupe_key text NOT NULL CHECK (char_length(dedupe_key) BETWEEN 1 AND 120),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    read_at timestamptz,
    resolved_at timestamptz,
    UNIQUE (owner_id,dedupe_key),
    FOREIGN KEY (owner_id,source_conflict_id) REFERENCES public.sync_conflicts(owner_id,id) ON DELETE CASCADE
);
CREATE INDEX pr11c_notification_page ON public.notifications(owner_id,created_at DESC,id DESC);
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.notifications FROM PUBLIC;
CREATE POLICY notification_visible ON public.notifications TO classroom_web_runtime
    USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
      AND public.classroom_web_owner_active()
      AND (class_session_id IS NULL OR public.classroom_web_qa_class_live(class_session_id))
      AND (source_conflict_id IS NULL OR EXISTS (SELECT FROM public.sync_conflicts c
           WHERE (c.owner_id,c.id)=(notifications.owner_id,notifications.source_conflict_id)))
      AND (kind<>'task_completed' OR EXISTS (
          SELECT FROM public.tasks t
          WHERE (t.owner_id,t.id)=(notifications.owner_id,notifications.object_id)
            AND NOT public.classroom_web_tombstoned('account',t.owner_id)
            AND (t.class_session_id IS NULL OR public.classroom_web_qa_class_live(t.class_session_id))
            AND (t.plan_id IS NULL OR public.classroom_web_qa_plan_live(t.plan_id))
            AND CASE t.schema_version
              WHEN 'pr06b-1' THEN EXISTS (SELECT FROM public.asr_streams s
                  WHERE (s.owner_id,s.task_id)=(t.owner_id,t.id))
              WHEN 'pr06c-1' THEN EXISTS (SELECT FROM public.filetrans_runs r
                  WHERE (r.owner_id,r.task_id)=(t.owner_id,t.id))
              WHEN 'pr08c-1' THEN EXISTS (SELECT FROM public.portable_task_bindings b
                  WHERE (b.owner_id,b.task_id)=(t.owner_id,t.id))
              ELSE true
            END)));
GRANT SELECT, UPDATE (read_at) ON public.notifications TO classroom_web_runtime;
CREATE POLICY notification_writer ON public.notifications TO classroom_web_notification_writer
    USING (true) WITH CHECK (true);
GRANT SELECT (id,owner_id,dedupe_key,resolved_at), INSERT, UPDATE (resolved_at)
    ON public.notifications TO classroom_web_notification_writer;
CREATE POLICY notification_counter ON public.owner_event_counters TO classroom_web_notification_writer
    USING (true) WITH CHECK (true);
GRANT SELECT (owner_id,next_sequence), INSERT (id,owner_id,next_sequence), UPDATE (next_sequence)
    ON public.owner_event_counters TO classroom_web_notification_writer;
CREATE POLICY notification_event ON public.outbox_events FOR INSERT TO classroom_web_notification_writer
    WITH CHECK (event_type='notification.created' AND aggregate_type='notification');
GRANT INSERT (id,owner_id,event_sequence,event_type,aggregate_type,aggregate_id,payload,schema_version)
    ON public.outbox_events TO classroom_web_notification_writer;
CREATE POLICY notification_expiry ON public.deletion_executions FOR SELECT TO classroom_web_notification_writer
    USING (origin='expiry');
GRANT SELECT (owner_id,request_id,origin) ON public.deletion_executions TO classroom_web_notification_writer;
CREATE POLICY notification_audio ON public.media_assets FOR SELECT TO classroom_web_notification_writer
    USING (kind='audio_chunk' AND class_session_id IS NOT NULL AND confirmed_at IS NOT NULL);
GRANT SELECT (owner_id,id,kind,class_session_id,confirmed_at) ON public.media_assets TO classroom_web_notification_writer;

-- Writer has no login, members or public EXECUTE. Source triggers pass fixed
-- message codes, and only the insert winner allocates a sequence and publishes.
CREATE FUNCTION public.classroom_web_create_notification(target_owner uuid, target_object uuid, target_class uuid,
    target_conflict uuid, new_kind text, new_severity text, code text, dedupe text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $notice$
DECLARE identity uuid; sequence bigint; event uuid;
BEGIN
    INSERT INTO public.notifications(id,owner_id,object_id,class_session_id,source_conflict_id,
        kind,severity,message_code,dedupe_key)
    VALUES (gen_random_uuid(),target_owner,target_object,target_class,target_conflict,new_kind,new_severity,code,dedupe)
    ON CONFLICT (owner_id,dedupe_key) DO NOTHING RETURNING id INTO identity;
    IF identity IS NULL THEN RETURN; END IF;
    INSERT INTO public.owner_event_counters(id,owner_id,next_sequence)
      VALUES(gen_random_uuid(),target_owner,1) ON CONFLICT DO NOTHING;
    UPDATE public.owner_event_counters SET next_sequence=next_sequence+1
      WHERE owner_id=target_owner RETURNING next_sequence-1 INTO sequence;
    event := gen_random_uuid();
    INSERT INTO public.outbox_events(id,owner_id,event_sequence,event_type,aggregate_type,aggregate_id,payload,schema_version)
      VALUES (event,target_owner,sequence,'notification.created','notification',identity,
        jsonb_build_object('notification_id',identity,'severity',new_severity,'message_code',code),'1');
    PERFORM pg_notify('classroom_web_events',event::text);
END
$notice$;
ALTER FUNCTION public.classroom_web_create_notification(uuid,uuid,uuid,uuid,text,text,text,text)
    OWNER TO classroom_web_notification_writer;
REVOKE ALL ON FUNCTION public.classroom_web_create_notification(uuid,uuid,uuid,uuid,text,text,text,text) FROM PUBLIC;

CREATE FUNCTION public.classroom_web_operations_outbox() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $notice$
DECLARE classroom uuid;
BEGIN
    IF NEW.event_type='task.changed' AND NEW.payload->>'state'='succeeded' THEN
        PERFORM public.classroom_web_create_notification(NEW.owner_id,NEW.aggregate_id,
            (NEW.payload->>'class_session_id')::uuid,NULL,'task_completed','info',
            'TASK_COMPLETED','task:'||NEW.aggregate_id);
    ELSIF NEW.event_type='capture.lease_lost' THEN
        PERFORM public.classroom_web_create_notification(NEW.owner_id,NEW.aggregate_id,NEW.aggregate_id,
            NULL,'capture_failure','warning','CAPTURE_LEASE_LOST','capture:'||(NEW.payload->>'capture_epoch_id'));
    ELSIF NEW.event_type='object.deleted' AND NEW.aggregate_type='media_asset'
        AND NEW.payload->>'state' IN ('access_revoked','pending_delete')
        AND EXISTS (SELECT FROM public.deletion_executions d WHERE d.owner_id=NEW.owner_id
                    AND d.request_id=(NEW.payload->>'deletion_id')::uuid AND d.origin='expiry') THEN
        SELECT a.class_session_id INTO classroom FROM public.media_assets a
          WHERE (a.owner_id,a.id)=(NEW.owner_id,NEW.aggregate_id) AND a.kind='audio_chunk';
        IF classroom IS NOT NULL THEN
            PERFORM public.classroom_web_create_notification(NEW.owner_id,classroom,classroom,NULL,
                'audio_expiry','info','AUDIO_EXPIRED','audio-expiry:'||classroom);
        END IF;
    END IF;
    RETURN NULL;
END
$notice$;
ALTER FUNCTION public.classroom_web_operations_outbox() OWNER TO classroom_web_notification_writer;
REVOKE ALL ON FUNCTION public.classroom_web_operations_outbox() FROM PUBLIC;
CREATE TRIGGER pr11c_operations_outbox AFTER INSERT ON public.outbox_events
  FOR EACH ROW WHEN (NEW.event_type IN ('task.changed','capture.lease_lost','object.deleted'))
  EXECUTE FUNCTION public.classroom_web_operations_outbox();

CREATE FUNCTION public.classroom_web_operations_sync() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $notice$
BEGIN
    IF TG_TABLE_NAME='sync_resolutions' THEN
        UPDATE public.notifications SET resolved_at=coalesce(resolved_at,clock_timestamp())
          WHERE owner_id=NEW.owner_id AND dedupe_key='conflict:'||NEW.conflict_id;
    ELSE
        PERFORM public.classroom_web_create_notification(NEW.owner_id,NEW.id,NEW.class_session_id,
            NEW.id,'conflict','warning','SYNC_CONFLICT','conflict:'||NEW.id);
    END IF;
    RETURN NULL;
END
$notice$;
ALTER FUNCTION public.classroom_web_operations_sync() OWNER TO classroom_web_notification_writer;
REVOKE ALL ON FUNCTION public.classroom_web_operations_sync() FROM PUBLIC;
CREATE TRIGGER pr11c_sync_conflict AFTER INSERT ON public.sync_conflicts
    FOR EACH ROW EXECUTE FUNCTION public.classroom_web_operations_sync();
CREATE TRIGGER pr11c_sync_resolution AFTER INSERT ON public.sync_resolutions
    FOR EACH ROW EXECUTE FUNCTION public.classroom_web_operations_sync();

-- The migration bootstrap sees all owners. Use the same writer as the trigger
-- so each open conflict has the same dedupe key, payload and outbox sequence.
SELECT public.classroom_web_create_notification(c.owner_id,c.id,c.class_session_id,
    c.id,'conflict','warning','SYNC_CONFLICT','conflict:'||c.id)
FROM public.sync_conflicts c
WHERE NOT EXISTS (SELECT FROM public.sync_resolutions r
    WHERE (r.owner_id,r.conflict_id)=(c.owner_id,c.id))
ORDER BY c.owner_id,c.created_at,c.id;

CREATE TABLE public.feedback_reports (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES public.users(id),
    category text NOT NULL CHECK (category IN ('bug','asr','ai_content','ui','sync','security','other')),
    build_id text NOT NULL CHECK (char_length(build_id)<=100),
    description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 5000),
    request_id uuid,
    consent boolean NOT NULL,
    confirmed_excerpt text CHECK (consent AND char_length(confirmed_excerpt) BETWEEN 1 AND 5000
        OR confirmed_excerpt IS NULL),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.feedback_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feedback_reports FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.feedback_reports FROM PUBLIC;
CREATE POLICY feedback_submit ON public.feedback_reports FOR INSERT TO classroom_web_runtime
    WITH CHECK (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND public.classroom_web_owner_active());
GRANT INSERT ON public.feedback_reports TO classroom_web_runtime;
-- Match the account scope used by 04b and grant the projection owner only
-- the columns needed to see committed account revocations.
CREATE POLICY feedback_account_tombstone ON public.deletion_tombstones
    FOR SELECT TO classroom_web_feedback_projection
    USING (resource_type='account' AND resource_id=owner_id);
GRANT SELECT (owner_id) ON public.deletion_tombstones TO classroom_web_feedback_projection;
GRANT SELECT (id,state,deleted_at) ON public.users TO classroom_web_feedback_projection;

-- 04b's in-memory admission decision also gates direct SQL reader roles.
-- A copied database or a new PostgreSQL startup must obtain a new decision.
CREATE SCHEMA pr11c_feedback;
REVOKE ALL ON SCHEMA pr11c_feedback FROM PUBLIC;
CREATE UNLOGGED TABLE pr11c_feedback.deletion_admission (
    database_oid oid PRIMARY KEY,
    gate_relation_oid oid NOT NULL,
    server_started_at timestamptz NOT NULL,
    admitted_xid xid8 NOT NULL
);
GRANT USAGE ON SCHEMA pr11c_feedback TO classroom_web_eraser, classroom_web_feedback_projection;
GRANT INSERT, UPDATE, SELECT, DELETE ON pr11c_feedback.deletion_admission TO classroom_web_eraser;
GRANT SELECT ON pr11c_feedback.deletion_admission TO classroom_web_feedback_projection;

CREATE FUNCTION public.classroom_web_feedback_admit(ready boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $gate$
BEGIN
    LOCK TABLE pr11c_feedback.deletion_admission IN SHARE ROW EXCLUSIVE MODE;
    DELETE FROM pr11c_feedback.deletion_admission;
    IF ready THEN
        INSERT INTO pr11c_feedback.deletion_admission(database_oid,gate_relation_oid,server_started_at,admitted_xid)
          SELECT oid,'pr11c_feedback.deletion_admission'::regclass::oid,pg_postmaster_start_time(),pg_current_xact_id()
          FROM pg_database WHERE datname=current_database() AND NOT pg_is_in_recovery();
    END IF;
END
$gate$;
ALTER FUNCTION public.classroom_web_feedback_admit(boolean) OWNER TO classroom_web_eraser;
REVOKE ALL ON FUNCTION public.classroom_web_feedback_admit(boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_feedback_admit(boolean)
    TO classroom_web_deletion_worker,classroom_web_restore_operator;

CREATE POLICY feedback_projection ON public.feedback_reports FOR SELECT TO classroom_web_feedback_projection
    USING (current_setting('transaction_isolation')='read committed' AND NOT pg_is_in_recovery()
       AND EXISTS (SELECT FROM pr11c_feedback.deletion_admission a
           JOIN pg_catalog.pg_database d ON d.oid=a.database_oid
           WHERE d.datname=current_database()
             AND a.gate_relation_oid='pr11c_feedback.deletion_admission'::regclass::oid
             AND a.server_started_at=pg_postmaster_start_time()
             AND a.xmin=a.admitted_xid::xid)
       AND NOT EXISTS (SELECT FROM public.deletion_tombstones t WHERE t.owner_id=feedback_reports.owner_id)
       AND NOT EXISTS (SELECT FROM public.users u WHERE u.id=feedback_reports.owner_id
           AND (u.state IN ('erasing','erased') OR u.deleted_at IS NOT NULL)));
GRANT SELECT (id,category,build_id,request_id,consent,created_at,confirmed_excerpt)
    ON public.feedback_reports TO classroom_web_feedback_projection;

CREATE VIEW public.feedback_admin_metadata WITH (security_barrier=true) AS
    SELECT id,category,build_id,request_id,consent,created_at FROM public.feedback_reports;
CREATE VIEW public.feedback_authorized_excerpts WITH (security_barrier=true) AS
    SELECT id,confirmed_excerpt FROM public.feedback_reports WHERE consent AND confirmed_excerpt IS NOT NULL;
ALTER VIEW public.feedback_admin_metadata OWNER TO classroom_web_feedback_projection;
ALTER VIEW public.feedback_authorized_excerpts OWNER TO classroom_web_feedback_projection;
REVOKE ALL ON public.feedback_admin_metadata, public.feedback_authorized_excerpts FROM PUBLIC;
-- No direct reader grants in Alpha: a feedback reader must later go through a bounded,
-- paged interface that rechecks admission and tombstones per page (Astra r0-r3 findings).
REVOKE ALL ON public.feedback_admin_metadata, public.feedback_authorized_excerpts
    FROM classroom_web_feedback_admin, classroom_web_feedback_excerpt, classroom_web_runtime;
