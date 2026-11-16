-- PR-07b adds durable learning artifacts without enabling model dispatch.
-- Global review snapshots freeze all selected source units. Individual batches
-- retain their own bounded subset of these exact aliases.
ALTER TABLE source_snapshots DROP CONSTRAINT source_snapshots_source_ids_check;
ALTER TABLE source_snapshots ADD CONSTRAINT source_snapshots_source_ids_check
 CHECK(cardinality(source_ids) BETWEEN 1 AND 512);
ALTER TABLE source_items DROP CONSTRAINT source_items_source_key_check;
ALTER TABLE source_items ADD CONSTRAINT source_items_source_key_check
 CHECK(source_key ~ '^S[1-9][0-9]{0,2}$');

CREATE TABLE review_batches (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 plan_id uuid NOT NULL, class_session_id uuid NOT NULL,
 batch_index integer NOT NULL CHECK(batch_index>=0), snapshot_id uuid NOT NULL,
 source_keys jsonb NOT NULL CHECK(jsonb_typeof(source_keys)='array'
   AND jsonb_array_length(source_keys) BETWEEN 1 AND 512 AND pg_column_size(source_keys)<=16384),
 task_id uuid, child_plan_id uuid,
 UNIQUE(owner_id,id), UNIQUE(owner_id,plan_id,batch_index), UNIQUE(owner_id,task_id),
 FOREIGN KEY(owner_id,plan_id,class_session_id) REFERENCES generation_plans(owner_id,id,class_session_id),
 FOREIGN KEY(owner_id,snapshot_id,class_session_id) REFERENCES source_snapshots(owner_id,id,class_session_id),
 FOREIGN KEY(owner_id,task_id) REFERENCES tasks(owner_id,id),
 FOREIGN KEY(owner_id,child_plan_id,class_session_id) REFERENCES generation_plans(owner_id,id,class_session_id),
 CHECK((task_id IS NULL)=(child_plan_id IS NULL))
);
ALTER TABLE review_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_batches FORCE ROW LEVEL SECURITY;
CREATE POLICY review_batches_owner ON review_batches TO classroom_web_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND classroom_web_qa_plan_live(plan_id));
GRANT SELECT,INSERT,UPDATE ON review_batches TO classroom_web_runtime;

CREATE FUNCTION classroom_web_guard_review_batch() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
DECLARE plan_snapshot uuid; batch_source_key jsonb;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'review batch manifest is immutable' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-ARRAY['task_id','child_plan_id'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['task_id','child_plan_id'])
      OR OLD.task_id IS NOT NULL OR OLD.child_plan_id IS NOT NULL
      OR NEW.task_id IS NULL OR NEW.child_plan_id IS NULL) THEN
  RAISE EXCEPTION 'review batch may bind its task and child plan only once' USING ERRCODE='23514';
 END IF;
 SELECT snapshot_id INTO plan_snapshot FROM generation_plans
  WHERE owner_id=NEW.owner_id AND id=NEW.plan_id AND class_session_id=NEW.class_session_id AND kind='review_summary';
 IF plan_snapshot IS DISTINCT FROM NEW.snapshot_id THEN
  RAISE EXCEPTION 'review batch must preserve its root plan snapshot' USING ERRCODE='23514';
 END IF;
 IF jsonb_typeof(NEW.source_keys) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'review batch sources must be an array' USING ERRCODE='23514';
 END IF;
 IF jsonb_array_length(NEW.source_keys)=0
    OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.source_keys))<>jsonb_array_length(NEW.source_keys) THEN
  RAISE EXCEPTION 'review batch sources must be nonempty and unique' USING ERRCODE='23514';
 END IF;
 FOR batch_source_key IN SELECT value FROM jsonb_array_elements(NEW.source_keys) LOOP
  IF jsonb_typeof(batch_source_key)<>'string' OR NOT EXISTS(SELECT FROM source_items s
     WHERE s.owner_id=NEW.owner_id AND s.snapshot_id=NEW.snapshot_id AND to_jsonb(s.source_key)=batch_source_key) THEN
   RAISE EXCEPTION 'review batch source must belong to its frozen snapshot' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF NEW.task_id IS NOT NULL THEN
  IF NOT EXISTS(SELECT FROM tasks t JOIN generation_plans p ON p.owner_id=t.owner_id AND p.id=t.plan_id
      WHERE t.owner_id=NEW.owner_id AND t.id=NEW.task_id AND t.class_session_id=NEW.class_session_id
      AND t.plan_id=NEW.child_plan_id AND p.snapshot_id=NEW.snapshot_id AND p.kind='review_summary'
      AND t.kind='review_summary'
      AND ((NEW.batch_index=0 AND NEW.child_plan_id=NEW.plan_id AND t.schema_version='pr07b-1')
           OR (NEW.batch_index>0 AND NEW.child_plan_id<>NEW.plan_id AND t.schema_version='pr07b-batch-1'))) THEN
   RAISE EXCEPTION 'review batch task must match its exact plan and protocol' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_review_batch() FROM PUBLIC;
CREATE TRIGGER review_batch_guard BEFORE INSERT OR UPDATE OR DELETE ON review_batches
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_review_batch();

CREATE TABLE review_documents (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),
 class_session_id uuid NOT NULL, kind text NOT NULL CHECK(kind IN ('summary','card','note')),
 title text NOT NULL CHECK(char_length(title)<=200),
 origin_snapshot_id uuid, parent_document_id uuid, artifact_key text NOT NULL CHECK(char_length(artifact_key) BETWEEN 1 AND 100),
 adopted_revision_id uuid, latest_personal_revision_id uuid,
 understood boolean NOT NULL DEFAULT false,
 UNIQUE(owner_id,id), UNIQUE(owner_id,id,class_session_id),
 UNIQUE(owner_id,parent_document_id,artifact_key),
 FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id),
 FOREIGN KEY(owner_id,origin_snapshot_id,class_session_id) REFERENCES source_snapshots(owner_id,id,class_session_id),
 FOREIGN KEY(owner_id,parent_document_id,class_session_id) REFERENCES review_documents(owner_id,id,class_session_id),
 CHECK((kind='note' AND origin_snapshot_id IS NULL AND parent_document_id IS NULL) OR
       (kind='summary' AND origin_snapshot_id IS NOT NULL AND parent_document_id IS NULL) OR
       (kind='card' AND origin_snapshot_id IS NOT NULL AND parent_document_id IS NOT NULL))
);
CREATE TABLE review_revisions (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 document_id uuid NOT NULL, class_session_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>=1), kind text NOT NULL CHECK(kind IN ('ai_candidate','personal')),
 parent_revision_id uuid, source_snapshot_id uuid, raw_output_ref uuid,
 task_id uuid, artifact_key text, draft_id uuid,
 body_markdown text NOT NULL CHECK(char_length(body_markdown)<=120000),
 structured_content jsonb CHECK(structured_content IS NULL OR (jsonb_typeof(structured_content)='object' AND pg_column_size(structured_content)<=1048576)),
 personal_note text NOT NULL DEFAULT '' CHECK(char_length(personal_note)<=12000),
 UNIQUE(owner_id,id), UNIQUE(owner_id,document_id,id), UNIQUE(owner_id,document_id,revision),
 UNIQUE(owner_id,task_id,artifact_key),
 FOREIGN KEY(owner_id,document_id,class_session_id) REFERENCES review_documents(owner_id,id,class_session_id),
 FOREIGN KEY(owner_id,document_id,parent_revision_id) REFERENCES review_revisions(owner_id,document_id,id),
 FOREIGN KEY(owner_id,source_snapshot_id,class_session_id) REFERENCES source_snapshots(owner_id,id,class_session_id),
 FOREIGN KEY(owner_id,task_id) REFERENCES tasks(owner_id,id),
 FOREIGN KEY(owner_id,raw_output_ref) REFERENCES provider_results(owner_id,id),
 CHECK((kind='ai_candidate' AND source_snapshot_id IS NOT NULL AND task_id IS NOT NULL AND artifact_key IS NOT NULL AND draft_id IS NULL AND structured_content IS NOT NULL) OR
       (kind='personal' AND task_id IS NULL AND artifact_key IS NULL AND draft_id IS NOT NULL AND raw_output_ref IS NULL AND structured_content IS NULL))
);
ALTER TABLE review_documents ADD CONSTRAINT fk_review_adopted
 FOREIGN KEY(owner_id,id,adopted_revision_id) REFERENCES review_revisions(owner_id,document_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE review_documents ADD CONSTRAINT fk_review_personal
 FOREIGN KEY(owner_id,id,latest_personal_revision_id) REFERENCES review_revisions(owner_id,document_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE review_document_history (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 document_id uuid NOT NULL, revision bigint NOT NULL CHECK(revision>=1),
 projection jsonb NOT NULL CHECK(jsonb_typeof(projection)='object' AND pg_column_size(projection)<=131072),
 UNIQUE(owner_id,id), UNIQUE(owner_id,document_id,revision),
 FOREIGN KEY(owner_id,document_id) REFERENCES review_documents(owner_id,id)
);
CREATE TABLE review_conflicts (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 document_id uuid NOT NULL, submitted_revision_id uuid NOT NULL,
 current_revision_id uuid, current_document_revision bigint NOT NULL CHECK(current_document_revision>=1),
 UNIQUE(owner_id,id), UNIQUE(owner_id,submitted_revision_id),
 FOREIGN KEY(owner_id,document_id) REFERENCES review_documents(owner_id,id),
 FOREIGN KEY(owner_id,document_id,submitted_revision_id) REFERENCES review_revisions(owner_id,document_id,id),
 FOREIGN KEY(owner_id,document_id,current_revision_id) REFERENCES review_revisions(owner_id,document_id,id)
);
CREATE INDEX ix_review_documents_class ON review_documents(owner_id,class_session_id,created_at,id);
CREATE INDEX ix_review_revisions_document ON review_revisions(owner_id,document_id,revision);

ALTER TABLE review_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_documents FORCE ROW LEVEL SECURITY;
CREATE POLICY review_documents_owner ON review_documents TO classroom_web_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND classroom_web_qa_class_live(class_session_id)
   AND (origin_snapshot_id IS NULL OR EXISTS(SELECT FROM source_snapshots s WHERE s.owner_id=review_documents.owner_id AND s.id=origin_snapshot_id)));
GRANT SELECT,INSERT,UPDATE ON review_documents TO classroom_web_runtime;
ALTER TABLE review_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY review_revisions_owner ON review_revisions TO classroom_web_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND EXISTS(SELECT FROM review_documents d WHERE d.owner_id=review_revisions.owner_id AND d.id=document_id)
   AND (source_snapshot_id IS NULL OR EXISTS(SELECT FROM source_snapshots s WHERE s.owner_id=review_revisions.owner_id AND s.id=source_snapshot_id)));
GRANT SELECT,INSERT ON review_revisions TO classroom_web_runtime;
ALTER TABLE review_document_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_document_history FORCE ROW LEVEL SECURITY;
CREATE POLICY review_history_owner ON review_document_history TO classroom_web_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND EXISTS(SELECT FROM review_documents d WHERE d.owner_id=review_document_history.owner_id AND d.id=document_id));
GRANT SELECT,INSERT ON review_document_history TO classroom_web_runtime;
ALTER TABLE review_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_conflicts FORCE ROW LEVEL SECURITY;
CREATE POLICY review_conflicts_owner ON review_conflicts TO classroom_web_runtime
 USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND EXISTS(SELECT FROM review_revisions r WHERE r.owner_id=review_conflicts.owner_id AND r.id=submitted_revision_id));
GRANT SELECT,INSERT ON review_conflicts TO classroom_web_runtime;
CREATE TRIGGER review_revisions_immutable BEFORE UPDATE OR DELETE ON review_revisions
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();
CREATE TRIGGER review_history_immutable BEFORE UPDATE OR DELETE ON review_document_history
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();
CREATE TRIGGER review_conflicts_immutable BEFORE UPDATE OR DELETE ON review_conflicts
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();

CREATE FUNCTION classroom_web_guard_review_document() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-ARRAY['title','adopted_revision_id','latest_personal_revision_id','understood','revision','updated_at'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['title','adopted_revision_id','latest_personal_revision_id','understood','revision','updated_at'])
    OR NEW.revision<>OLD.revision+1) THEN
  RAISE EXCEPTION 'review identity is immutable and changes require one revision' USING ERRCODE='23514';
 END IF;
 IF NEW.latest_personal_revision_id IS NOT NULL
    AND (TG_OP='INSERT' OR NEW.latest_personal_revision_id IS DISTINCT FROM OLD.latest_personal_revision_id)
    AND NOT EXISTS(SELECT FROM review_revisions r
     WHERE r.owner_id=NEW.owner_id AND r.document_id=NEW.id AND r.id=NEW.latest_personal_revision_id AND r.kind='personal') THEN
  RAISE EXCEPTION 'personal pointer must reference a personal revision' USING ERRCODE='23514';
 END IF;
 IF NEW.parent_document_id IS NOT NULL AND NOT EXISTS(SELECT FROM review_documents d
     WHERE d.owner_id=NEW.owner_id AND d.id=NEW.parent_document_id AND d.kind='summary') THEN
  RAISE EXCEPTION 'card must belong to a summary document' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_review_document() FROM PUBLIC;
CREATE TRIGGER review_document_guard BEFORE INSERT OR UPDATE ON review_documents
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_review_document();
CREATE FUNCTION classroom_web_guard_review_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
DECLARE task_class uuid; task_snapshot uuid; result_task uuid; inherited_snapshot uuid; item jsonb; reference jsonb;
BEGIN
 IF NEW.kind='ai_candidate' THEN
  SELECT t.class_session_id,p.snapshot_id INTO task_class,task_snapshot FROM tasks t
   JOIN generation_plans p ON p.owner_id=t.owner_id AND p.id=t.plan_id
   WHERE t.owner_id=NEW.owner_id AND t.id=NEW.task_id AND t.kind='review_summary' AND t.schema_version='pr07b-1';
  IF task_class IS DISTINCT FROM NEW.class_session_id OR task_snapshot IS DISTINCT FROM NEW.source_snapshot_id THEN
   RAISE EXCEPTION 'review task must belong to this classroom' USING ERRCODE='23514';
  END IF;
  IF NEW.structured_content->'schema_version' IS DISTINCT FROM '1'::jsonb
     OR jsonb_typeof(NEW.structured_content->'cards') IS DISTINCT FROM 'array'
     OR jsonb_typeof(NEW.structured_content->'notes') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'review content must use the supported structure' USING ERRCODE='23514';
  END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(NEW.structured_content->'cards')
              UNION ALL SELECT value FROM jsonb_array_elements(NEW.structured_content->'notes') LOOP
   IF jsonb_typeof(item->'source_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(item->'source_ids')=0 THEN
    RAISE EXCEPTION 'review entry requires source references' USING ERRCODE='23514';
   END IF;
   FOR reference IN SELECT value FROM jsonb_array_elements(item->'source_ids') LOOP
    IF jsonb_typeof(reference)<>'string' OR NOT EXISTS(SELECT FROM source_items s
       WHERE s.owner_id=NEW.owner_id AND s.snapshot_id=NEW.source_snapshot_id AND to_jsonb(s.source_key)=reference) THEN
     RAISE EXCEPTION 'review reference must belong to the frozen snapshot' USING ERRCODE='23514';
    END IF;
   END LOOP;
  END LOOP;
  IF NEW.raw_output_ref IS NOT NULL THEN
   SELECT a.task_id INTO result_task FROM provider_results r JOIN task_attempts a
    ON a.owner_id=r.owner_id AND a.id=r.attempt_id WHERE r.owner_id=NEW.owner_id AND r.id=NEW.raw_output_ref;
   IF result_task IS DISTINCT FROM NEW.task_id THEN
    RAISE EXCEPTION 'review raw result must belong to this task' USING ERRCODE='23514';
   END IF;
  END IF;
 ELSIF NEW.parent_revision_id IS NOT NULL THEN
  SELECT source_snapshot_id INTO inherited_snapshot FROM review_revisions
   WHERE owner_id=NEW.owner_id AND document_id=NEW.document_id AND id=NEW.parent_revision_id;
  IF inherited_snapshot IS DISTINCT FROM NEW.source_snapshot_id THEN
   RAISE EXCEPTION 'personal revision must preserve parent provenance' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.source_snapshot_id IS NOT NULL OR EXISTS(SELECT FROM review_documents
   WHERE owner_id=NEW.owner_id AND id=NEW.document_id AND kind<>'note') THEN
  RAISE EXCEPTION 'generated content edits require a parent revision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_review_revision() FROM PUBLIC;
CREATE TRIGGER review_revision_guard BEFORE INSERT ON review_revisions
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_review_revision();

ALTER TABLE generation_plans DROP CONSTRAINT generation_plans_kind_check;
ALTER TABLE generation_plans ADD CONSTRAINT generation_plans_kind_check CHECK(kind IN ('ai_question','review_summary'));
ALTER TABLE tasks DROP CONSTRAINT ck_tasks_schema_version;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks_schema_version CHECK(schema_version IN ('1','pr07a-1','pr07b-1','pr07b-batch-1'));
ALTER TABLE tasks DROP CONSTRAINT ck_tasks_qa_plan;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks_qa_plan CHECK(
 (schema_version='pr07a-1' AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='ai_question') OR
 (schema_version IN ('pr07b-1','pr07b-batch-1') AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='review_summary') OR
 (schema_version='1' AND plan_id IS NULL));
ALTER TABLE operation_receipts DROP CONSTRAINT ck_operation_receipts_resource_type;
ALTER TABLE operation_receipts ADD CONSTRAINT ck_operation_receipts_resource_type CHECK(resource_type IN
 ('task','semester','course','class_session','course_material_version','media_asset','media_upload','generation_plan','question','review_document','review_revision','review_conflict'));
CREATE OR REPLACE FUNCTION classroom_web_guard_qa_plan() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF (to_jsonb(NEW)-ARRAY['consumed_task_id','consumed_at'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['consumed_task_id','consumed_at'])
    OR OLD.consumed_task_id IS NOT NULL OR NEW.consumed_task_id IS NULL THEN
  RAISE EXCEPTION 'QA plan can be consumed once' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT FROM tasks t WHERE t.owner_id=NEW.owner_id AND t.id=NEW.consumed_task_id
    AND t.plan_id=NEW.id AND t.class_session_id=NEW.class_session_id AND t.kind=NEW.kind
    AND ((NEW.kind='ai_question' AND t.schema_version='pr07a-1') OR
         (NEW.kind='review_summary' AND t.schema_version IN ('pr07b-1','pr07b-batch-1')))) THEN
  RAISE EXCEPTION 'consumed task must belong to this plan and classroom' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
