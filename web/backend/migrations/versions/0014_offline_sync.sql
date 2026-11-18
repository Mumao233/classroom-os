-- Separate private command bodies from the generic minimized operation receipts.
ALTER TABLE operation_receipts DROP CONSTRAINT ck_operation_receipts_resource_type;
ALTER TABLE operation_receipts ADD CONSTRAINT ck_operation_receipts_resource_type
 CHECK(resource_type IN ('task','semester','course','class_session','course_material_version',
  'media_asset','media_upload','generation_plan','question','review_document','review_revision',
  'review_conflict','deletion_request','mark','sync_operation','sync_batch'));

CREATE TABLE sync_operations (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 operation text NOT NULL, op_id uuid NOT NULL, request_hash text NOT NULL,
 object_type text NOT NULL, object_id uuid NOT NULL, class_session_id uuid,
 result_revision bigint, conflict_id uuid,
 status text NOT NULL CHECK(status IN ('applied','conflict','deleted','rejected')),
 UNIQUE(owner_id,id), UNIQUE(owner_id,operation,op_id),
 FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id),
 CHECK(result_revision IS NULL OR result_revision>=1),
 CHECK(request_hash ~ '^[a-f0-9]{64}$')
);
CREATE TABLE sync_conflicts (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 object_type text NOT NULL, object_id uuid NOT NULL, class_session_id uuid,
 base_revision bigint NOT NULL CHECK(base_revision>=1),
 server_revision bigint NOT NULL CHECK(server_revision>=1),
 local_operation jsonb NOT NULL CHECK(jsonb_typeof(local_operation)='object' AND pg_column_size(local_operation)<=2000000),
 server_content jsonb NOT NULL CHECK(jsonb_typeof(server_content)='object' AND pg_column_size(server_content)<=2000000),
 UNIQUE(owner_id,id),
 FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id)
);
CREATE TABLE sync_resolutions (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 conflict_id uuid NOT NULL, op_id uuid NOT NULL,
 UNIQUE(owner_id,id), UNIQUE(owner_id,conflict_id),
 FOREIGN KEY(owner_id,conflict_id) REFERENCES sync_conflicts(owner_id,id)
);
CREATE TABLE sync_preferences (
 id uuid PRIMARY KEY REFERENCES users(id), owner_id uuid NOT NULL UNIQUE REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),
 value jsonb NOT NULL,
 UNIQUE(owner_id,id), CHECK(id=owner_id)
);
CREATE TABLE sync_marks (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),
 class_session_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('important','unclear')),
 anchor jsonb NOT NULL CHECK(jsonb_typeof(anchor)='object' AND pg_column_size(anchor)<=8192),
 note text NOT NULL DEFAULT '' CHECK(char_length(note)<=2000),
 understood boolean NOT NULL DEFAULT false,
 UNIQUE(owner_id,id),
 FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id)
);
CREATE INDEX ix_sync_marks_class ON sync_marks(owner_id,class_session_id,created_at,id);
INSERT INTO sync_preferences(id,owner_id,created_at,updated_at,value)
 SELECT id,id,created_at,updated_at,preferences
 FROM users;
CREATE FUNCTION classroom_web_sync_initial_preferences() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $fn$
BEGIN
 INSERT INTO public.sync_preferences(id,owner_id,created_at,updated_at,value)
 VALUES(NEW.id,NEW.id,NEW.created_at,NEW.updated_at,NEW.preferences);
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_sync_initial_preferences() FROM PUBLIC;
CREATE TRIGGER sync_initial_preferences AFTER INSERT ON users
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_initial_preferences();
DO $policies$
DECLARE target text;
BEGIN
 FOREACH target IN ARRAY ARRAY['sync_operations','sync_conflicts','sync_resolutions','sync_preferences','sync_marks'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',target);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC',target);
  EXECUTE format($policy$CREATE POLICY sync_owner ON public.%I TO classroom_web_runtime
   USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND classroom_web_owner_active())
   WITH CHECK(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND classroom_web_owner_active())$policy$,target);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO classroom_web_runtime',target);
 END LOOP;
END
$policies$;
-- Conflict content must disappear with its source document or classroom.
CREATE POLICY sync_conflict_live ON sync_conflicts AS RESTRICTIVE TO classroom_web_runtime
 USING((class_session_id IS NULL OR classroom_web_qa_class_live(class_session_id))
  AND (object_type<>'personal_revision' OR EXISTS(SELECT FROM review_documents d JOIN review_revisions r
    ON r.owner_id=d.owner_id AND r.document_id=d.id WHERE d.owner_id=sync_conflicts.owner_id AND d.id=object_id
    AND r.id=(server_content->>'id')::uuid AND EXISTS(SELECT FROM review_conflicts original
      WHERE original.owner_id=sync_conflicts.owner_id AND original.id=sync_conflicts.id)))
  AND (object_type<>'mark' OR EXISTS(SELECT FROM sync_marks m WHERE m.owner_id=sync_conflicts.owner_id AND m.id=object_id))
  AND (object_type<>'course' OR EXISTS(SELECT FROM courses c JOIN semesters s ON s.owner_id=c.owner_id AND s.id=c.semester_id
    WHERE c.owner_id=sync_conflicts.owner_id AND c.id=object_id AND c.deleted_at IS NULL AND s.deleted_at IS NULL
    AND NOT classroom_web_tombstoned('course',c.id) AND NOT classroom_web_tombstoned('semester',s.id))));
CREATE POLICY sync_mark_live ON sync_marks AS RESTRICTIVE TO classroom_web_runtime
 USING(classroom_web_qa_class_live(class_session_id)
 AND CASE anchor->>'kind'
  WHEN 'review_revision' THEN EXISTS(SELECT FROM review_revisions r WHERE r.owner_id=sync_marks.owner_id
    AND r.id=(anchor->>'revision_id')::uuid AND r.document_id=(anchor->>'document_id')::uuid)
  WHEN 'source' THEN EXISTS(SELECT FROM qa_source_versions v WHERE v.owner_id=sync_marks.owner_id
    AND v.id=(anchor->'source_ref'->>'revision_id')::uuid AND v.source_id=(anchor->'source_ref'->>'id')::uuid)
  WHEN 'timepoint' THEN EXISTS(SELECT FROM capture_epochs e WHERE e.owner_id=sync_marks.owner_id
    AND e.id=(anchor->>'capture_epoch_id')::uuid AND e.class_session_id=sync_marks.class_session_id)
  ELSE false END);
GRANT UPDATE(revision,updated_at,value) ON sync_preferences TO classroom_web_runtime;
GRANT UPDATE(revision,updated_at,note,understood) ON sync_marks TO classroom_web_runtime;
CREATE FUNCTION classroom_web_sync_guard_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
DECLARE mutable text[];
BEGIN
 mutable=CASE TG_TABLE_NAME WHEN 'sync_marks' THEN ARRAY['revision','updated_at','note','understood']
           ELSE ARRAY['revision','updated_at','value'] END;
 IF (to_jsonb(NEW)-mutable) IS DISTINCT FROM (to_jsonb(OLD)-mutable) OR NEW.revision<>OLD.revision+1 THEN
  RAISE EXCEPTION 'sync identity is immutable and changes require one revision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_sync_guard_mutation() FROM PUBLIC;
CREATE TRIGGER sync_guard_mutation BEFORE UPDATE ON sync_marks
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_guard_mutation();
CREATE TRIGGER sync_guard_mutation BEFORE UPDATE ON sync_preferences
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_guard_mutation();
CREATE TRIGGER sync_operations_immutable BEFORE UPDATE OR DELETE ON sync_operations
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();
CREATE TRIGGER sync_conflicts_immutable BEFORE UPDATE OR DELETE ON sync_conflicts
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();
CREATE TRIGGER sync_resolutions_immutable BEFORE UPDATE OR DELETE ON sync_resolutions
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();

-- This returns one ownership bit for a fixed kind, never content or another
-- principal's existence. It lets a late operation distinguish a known revoked
-- local object from an arbitrary or foreign identifier after content RLS hides it.
CREATE FUNCTION classroom_web_sync_owned_object(kind text, identity uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $fn$
DECLARE actor uuid := nullif(current_setting('app.owner_id',true),'')::uuid;
BEGIN
 IF actor IS NULL OR NOT public.classroom_web_owner_active() THEN RETURN false; END IF;
 CASE kind
 WHEN 'personal_revision' THEN RETURN EXISTS(SELECT FROM public.review_documents WHERE owner_id=actor AND id=identity);
 WHEN 'mark' THEN RETURN EXISTS(SELECT FROM public.sync_marks WHERE owner_id=actor AND id=identity);
 WHEN 'course' THEN RETURN EXISTS(SELECT FROM public.courses WHERE owner_id=actor AND id=identity);
 WHEN 'preferences' THEN RETURN identity=actor;
 ELSE RETURN false;
 END CASE;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_sync_owned_object(text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_sync_owned_object(text,uuid) TO classroom_web_runtime;

-- Catalog writes through every existing entry point participate in the same
-- committed owner clock. Existing Review and deletion writers already do so.
CREATE FUNCTION classroom_web_sync_catalog_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
DECLARE sequence bigint; kind text; identity uuid;
BEGIN
 kind=CASE TG_TABLE_NAME WHEN 'semesters' THEN 'semester' WHEN 'courses' THEN 'course'
       WHEN 'class_sessions' THEN 'class_session' WHEN 'sync_marks' THEN 'mark' ELSE 'preferences' END;
 identity=NEW.id;
 INSERT INTO owner_event_counters(id,owner_id,next_sequence) VALUES(gen_random_uuid(),NEW.owner_id,1)
 ON CONFLICT(owner_id) DO NOTHING;
 UPDATE owner_event_counters SET next_sequence=next_sequence+1 WHERE owner_id=NEW.owner_id
 RETURNING next_sequence-1 INTO sequence;
 INSERT INTO outbox_events(id,owner_id,event_sequence,event_type,aggregate_type,aggregate_id,aggregate_revision,payload,schema_version)
 VALUES(gen_random_uuid(),NEW.owner_id,sequence,'object.changed',kind,identity,NEW.revision,
 jsonb_build_object('object_type',kind,'object_id',identity,'revision',NEW.revision),'1');
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_sync_catalog_event() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER sync_catalog_event AFTER INSERT OR UPDATE ON semesters
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_catalog_event();
CREATE CONSTRAINT TRIGGER sync_catalog_event AFTER INSERT OR UPDATE ON courses
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_catalog_event();
CREATE CONSTRAINT TRIGGER sync_catalog_event AFTER INSERT OR UPDATE ON class_sessions
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_catalog_event();
CREATE CONSTRAINT TRIGGER sync_catalog_event AFTER INSERT OR UPDATE ON sync_marks
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_catalog_event();
CREATE CONSTRAINT TRIGGER sync_catalog_event AFTER INSERT OR UPDATE ON sync_preferences
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW WHEN (NEW.revision>1) EXECUTE FUNCTION classroom_web_sync_catalog_event();

-- Source authority and source-status changes invalidate dependent cached Review
-- projections even when the document CAS itself has not changed. The trigger's
-- fixed owner/classroom lookup reads only IDs and revisions under its definer.
CREATE FUNCTION classroom_web_sync_source_events() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $fn$
DECLARE projection record; sequence bigint;
BEGIN
 FOR projection IN SELECT id,revision,'review_document'::text AS kind FROM public.review_documents
   WHERE owner_id=NEW.owner_id AND class_session_id=NEW.class_session_id
   UNION ALL SELECT id,revision,'mark'::text AS kind FROM public.sync_marks
   WHERE owner_id=NEW.owner_id AND class_session_id=NEW.class_session_id LOOP
  INSERT INTO public.owner_event_counters(id,owner_id,next_sequence)
   VALUES(gen_random_uuid(),NEW.owner_id,1) ON CONFLICT(owner_id) DO NOTHING;
  UPDATE public.owner_event_counters SET next_sequence=next_sequence+1 WHERE owner_id=NEW.owner_id
   RETURNING next_sequence-1 INTO sequence;
  INSERT INTO public.outbox_events(id,owner_id,event_sequence,event_type,aggregate_type,aggregate_id,aggregate_revision,payload,schema_version)
   VALUES(gen_random_uuid(),NEW.owner_id,sequence,'object.changed',projection.kind,projection.id,projection.revision,
   jsonb_build_object('object_type',projection.kind,'object_id',projection.id,'revision',projection.revision),'1');
 END LOOP;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_sync_source_events() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER sync_source_events AFTER INSERT OR UPDATE ON qa_sources
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION classroom_web_sync_source_events();
