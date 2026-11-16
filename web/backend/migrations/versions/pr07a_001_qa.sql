-- PR-07a is an additive, default-disabled integration. No provider is enabled here.
CREATE TABLE qa_sources (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  class_session_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('transcript_segment','course_material_version')),
  object_id uuid NOT NULL,
  current_revision_id uuid NOT NULL,
  origin text NOT NULL CHECK (origin IN ('manual','material','live_asr','import')),
  start_ms bigint,
  end_ms bigint,
  revoked_at timestamptz,
  UNIQUE(owner_id,id),
  UNIQUE(owner_id,id,class_session_id),
  FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id),
  CHECK ((start_ms IS NULL AND end_ms IS NULL) OR (start_ms >= 0 AND end_ms > start_ms AND end_ms<=9007199254740991)),
  CHECK ((kind='course_material_version' AND origin='material') OR kind='transcript_segment')
);
CREATE TABLE qa_source_versions (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  source_id uuid NOT NULL,
  class_session_id uuid NOT NULL,
  revision bigint NOT NULL CHECK (revision >= 1),
  text text NOT NULL CHECK (octet_length(text) BETWEEN 1 AND 32768),
  content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
  UNIQUE(owner_id,id),
  UNIQUE(owner_id,source_id,id),
  UNIQUE(owner_id,source_id,revision),
  FOREIGN KEY(owner_id,source_id,class_session_id) REFERENCES qa_sources(owner_id,id,class_session_id)
);
ALTER TABLE qa_sources ADD CONSTRAINT fk_qa_current_revision
  FOREIGN KEY(owner_id,id,current_revision_id) REFERENCES qa_source_versions(owner_id,source_id,id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE source_snapshots (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  class_session_id uuid NOT NULL,
  course_material_version_id uuid NOT NULL,
  scope jsonb NOT NULL CHECK (jsonb_typeof(scope)='object' AND pg_column_size(scope)<=16384),
  snapshot_hash char(64) NOT NULL CHECK (snapshot_hash ~ '^[a-f0-9]{64}$'),
  coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage)='object' AND pg_column_size(coverage)<=16384),
  source_ids uuid[] NOT NULL CHECK (cardinality(source_ids) BETWEEN 1 AND 12),
  UNIQUE(owner_id,id),
  UNIQUE(owner_id,id,class_session_id),
  FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id),
  FOREIGN KEY(owner_id,course_material_version_id) REFERENCES course_material_versions(owner_id,id)
);
CREATE TABLE source_items (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  snapshot_id uuid NOT NULL,
  class_session_id uuid NOT NULL,
  source_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  source_key text NOT NULL CHECK (source_key ~ '^S[1-9][0-9]?$'),
  frozen_text text NOT NULL CHECK (octet_length(frozen_text) BETWEEN 1 AND 32768),
  source_hash char(64) NOT NULL CHECK (source_hash ~ '^[a-f0-9]{64}$'),
  excerpt_sha256 char(64) NOT NULL CHECK (excerpt_sha256 ~ '^[a-f0-9]{64}$'),
  excerpt_start integer NOT NULL CHECK (excerpt_start >= 0),
  excerpt_end integer NOT NULL CHECK (excerpt_end > excerpt_start),
  token_bound integer NOT NULL CHECK (token_bound > 0),
  UNIQUE(owner_id,id),
  UNIQUE(owner_id,snapshot_id,source_key),
  CONSTRAINT uq_qa_excerpt UNIQUE(owner_id,snapshot_id,source_id,revision_id,excerpt_start,excerpt_end),
  FOREIGN KEY(owner_id,snapshot_id,class_session_id) REFERENCES source_snapshots(owner_id,id,class_session_id),
  FOREIGN KEY(owner_id,source_id,class_session_id) REFERENCES qa_sources(owner_id,id,class_session_id),
  FOREIGN KEY(owner_id,source_id,revision_id) REFERENCES qa_source_versions(owner_id,source_id,id)
);
CREATE TABLE generation_plans (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  class_session_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind='ai_question'),
  plan_hash char(64) NOT NULL CHECK (plan_hash ~ '^[a-f0-9]{64}$'),
  request jsonb NOT NULL CHECK (jsonb_typeof(request)='object' AND pg_column_size(request)<=32768),
  config jsonb NOT NULL CHECK (jsonb_typeof(config)='object' AND pg_column_size(config)<=8192),
  expires_at timestamptz NOT NULL,
  consumed_task_id uuid,
  consumed_at timestamptz,
  UNIQUE(owner_id,id),
  UNIQUE(owner_id,id,class_session_id),
  UNIQUE(owner_id,consumed_task_id),
  FOREIGN KEY(owner_id,snapshot_id,class_session_id) REFERENCES source_snapshots(owner_id,id,class_session_id),
  FOREIGN KEY(owner_id,consumed_task_id) REFERENCES tasks(owner_id,id) DEFERRABLE INITIALLY DEFERRED,
  CHECK ((consumed_task_id IS NULL) = (consumed_at IS NULL)),
  CHECK (expires_at > created_at)
);
CREATE TABLE questions (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  revision bigint NOT NULL DEFAULT 1 CHECK (revision>=1),
  class_session_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  task_id uuid NOT NULL,
  prompt text NOT NULL CHECK (char_length(prompt) BETWEEN 1 AND 12000),
  answer_markdown text CHECK (char_length(answer_markdown)<=120000),
  cited_source_ids jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(cited_source_ids)='array' AND jsonb_array_length(cited_source_ids)<=12),
  understood boolean NOT NULL DEFAULT false,
  UNIQUE(owner_id,id),
  UNIQUE(owner_id,task_id),
  FOREIGN KEY(owner_id,snapshot_id,class_session_id) REFERENCES source_snapshots(owner_id,id,class_session_id),
  FOREIGN KEY(owner_id,task_id) REFERENCES tasks(owner_id,id)
);
CREATE INDEX ix_qa_sources_class ON qa_sources(owner_id,class_session_id,created_at,id);
CREATE INDEX ix_qa_versions_source ON qa_source_versions(owner_id,source_id,revision);
CREATE INDEX ix_qa_plans_owner_created ON generation_plans(owner_id,created_at);
CREATE INDEX ix_qa_questions_class_created ON questions(owner_id,class_session_id,created_at,id);
CREATE INDEX ix_source_items_source ON source_items(owner_id,source_id);

CREATE FUNCTION classroom_web_qa_class_live(class_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=public,pg_temp AS $fn$
  SELECT EXISTS (
    SELECT FROM class_sessions s
      JOIN courses c ON c.owner_id=s.owner_id AND c.id=s.course_id
      JOIN semesters e ON e.owner_id=c.owner_id AND e.id=c.semester_id
      JOIN course_material_versions m ON m.owner_id=s.owner_id AND m.id=s.course_material_version_id
      JOIN courses mc ON mc.owner_id=m.owner_id AND mc.id=m.course_id
      JOIN semesters me ON me.owner_id=mc.owner_id AND me.id=mc.semester_id
    WHERE s.id=class_id AND s.owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
      AND classroom_web_owner_active()
      AND s.deleted_at IS NULL AND c.deleted_at IS NULL AND e.deleted_at IS NULL
      AND mc.deleted_at IS NULL AND me.deleted_at IS NULL
      AND NOT EXISTS (SELECT FROM deletion_tombstones d WHERE d.owner_id=s.owner_id AND (
        (d.resource_type='account' AND d.resource_id=s.owner_id)
        OR (d.resource_type='class_session' AND d.resource_id=s.id)
        OR (d.resource_type='course_material_version' AND d.resource_id=m.id)
        OR (d.resource_type='course' AND d.resource_id IN (c.id,mc.id))
        OR (d.resource_type='semester' AND d.resource_id IN (e.id,me.id)))))
$fn$;
REVOKE ALL ON FUNCTION classroom_web_qa_class_live(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_qa_class_live(uuid) TO classroom_web_runtime;

ALTER TABLE qa_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE qa_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY qa_sources_owner ON qa_sources TO classroom_web_runtime
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND revoked_at IS NULL
   AND classroom_web_qa_class_live(class_session_id))
 WITH CHECK (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND classroom_web_qa_class_live(class_session_id));
GRANT SELECT,INSERT,UPDATE ON qa_sources TO classroom_web_runtime;
ALTER TABLE qa_source_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE qa_source_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY qa_versions_owner ON qa_source_versions TO classroom_web_runtime
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND EXISTS (SELECT FROM qa_sources s WHERE s.owner_id=qa_source_versions.owner_id AND s.id=source_id));
GRANT SELECT,INSERT ON qa_source_versions TO classroom_web_runtime;

CREATE FUNCTION classroom_web_qa_sources_live(ids uuid[]) RETURNS boolean
LANGUAGE sql STABLE SET search_path=public,pg_temp AS $fn$
 SELECT cardinality(ids)>0 AND NOT EXISTS (
   SELECT FROM unnest(ids) AS source(id) WHERE NOT EXISTS (
     SELECT FROM qa_sources s WHERE s.id=source.id
       AND s.owner_id=nullif(current_setting('app.owner_id',true),'')::uuid))
$fn$;
REVOKE ALL ON FUNCTION classroom_web_qa_sources_live(uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_qa_sources_live(uuid[]) TO classroom_web_runtime;
ALTER TABLE source_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY qa_snapshots_owner ON source_snapshots TO classroom_web_runtime
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND classroom_web_qa_class_live(class_session_id) AND classroom_web_qa_sources_live(source_ids));
GRANT SELECT,INSERT ON source_snapshots TO classroom_web_runtime;
ALTER TABLE source_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_items FORCE ROW LEVEL SECURITY;
CREATE POLICY qa_items_owner ON source_items TO classroom_web_runtime
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND classroom_web_qa_class_live(class_session_id)
   AND EXISTS (SELECT FROM source_snapshots p WHERE p.owner_id=source_items.owner_id AND p.id=snapshot_id)
   AND EXISTS (SELECT FROM qa_sources s WHERE s.owner_id=source_items.owner_id AND s.id=source_id));
GRANT SELECT,INSERT ON source_items TO classroom_web_runtime;
ALTER TABLE generation_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_plans FORCE ROW LEVEL SECURITY;
CREATE POLICY qa_plans_owner ON generation_plans TO classroom_web_runtime
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND EXISTS (SELECT FROM source_snapshots s WHERE s.owner_id=generation_plans.owner_id AND s.id=snapshot_id));
GRANT SELECT,INSERT,UPDATE ON generation_plans TO classroom_web_runtime;
ALTER TABLE questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE questions FORCE ROW LEVEL SECURITY;
CREATE POLICY qa_questions_owner ON questions TO classroom_web_runtime
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid
   AND EXISTS (SELECT FROM source_snapshots s WHERE s.owner_id=questions.owner_id AND s.id=snapshot_id));
GRANT SELECT,INSERT,UPDATE ON questions TO classroom_web_runtime;

CREATE FUNCTION classroom_web_qa_plan_live(plan uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=public,pg_temp AS $fn$
 SELECT EXISTS (SELECT FROM generation_plans WHERE id=plan
   AND owner_id=nullif(current_setting('app.owner_id',true),'')::uuid)
$fn$;
REVOKE ALL ON FUNCTION classroom_web_qa_plan_live(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_qa_plan_live(uuid) TO classroom_web_runtime;

CREATE FUNCTION classroom_web_guard_qa_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF TG_OP<>'INSERT' THEN
   RAISE EXCEPTION 'QA frozen record is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_qa_immutable() FROM PUBLIC;
CREATE TRIGGER qa_versions_immutable BEFORE UPDATE OR DELETE ON qa_source_versions
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();
CREATE TRIGGER qa_snapshots_immutable BEFORE UPDATE OR DELETE ON source_snapshots
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();
CREATE TRIGGER qa_items_immutable BEFORE UPDATE OR DELETE ON source_items
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_immutable();

CREATE FUNCTION classroom_web_guard_qa_source() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
DECLARE material uuid;
BEGIN
 IF TG_OP='UPDATE' THEN
   IF OLD.revoked_at IS NOT NULL OR (to_jsonb(NEW)-ARRAY['current_revision_id','revoked_at'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['current_revision_id','revoked_at'])
      OR (NEW.revoked_at IS NOT NULL AND NEW.current_revision_id<>OLD.current_revision_id) THEN
     RAISE EXCEPTION 'QA source identity and revocation are immutable' USING ERRCODE='23514';
   END IF;
   IF NEW.revoked_at IS NOT NULL THEN
     -- Identity and current revision were checked above. This monotonic
     -- metadata command never needs catalog or source-content privileges.
     RETURN NEW;
   END IF;
 ELSIF NEW.revoked_at IS NOT NULL THEN
   RAISE EXCEPTION 'new QA source must be available' USING ERRCODE='23514';
 END IF;
 IF NEW.kind='course_material_version' AND (TG_OP='INSERT' OR NEW.current_revision_id<>OLD.current_revision_id) THEN
   SELECT course_material_version_id INTO material FROM class_sessions
     WHERE owner_id=NEW.owner_id AND id=NEW.class_session_id;
   IF NEW.object_id IS DISTINCT FROM material OR (TG_OP='UPDATE' AND NEW.current_revision_id<>OLD.current_revision_id) THEN
     RAISE EXCEPTION 'material source must retain classroom material version' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_qa_source() FROM PUBLIC;
CREATE TRIGGER qa_source_guard BEFORE INSERT OR UPDATE ON qa_sources
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_source();

-- A monotonic metadata-only command may revoke a row that SELECT must then hide.
-- PostgreSQL's SELECT checks on UPDATE otherwise prevent that transition.
DO $role$
BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='classroom_web_qa_revoker') THEN
   CREATE ROLE classroom_web_qa_revoker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END
$role$;
GRANT USAGE ON SCHEMA public TO classroom_web_qa_revoker;
GRANT SELECT(id,owner_id,revoked_at), UPDATE(revoked_at) ON qa_sources TO classroom_web_qa_revoker;
GRANT EXECUTE ON FUNCTION classroom_web_owner_active(),classroom_web_lock_owner() TO classroom_web_qa_revoker;
CREATE POLICY qa_revoke_metadata_read ON qa_sources FOR SELECT TO classroom_web_qa_revoker
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND classroom_web_owner_active());
CREATE POLICY qa_revoke_metadata_update ON qa_sources FOR UPDATE TO classroom_web_qa_revoker
 USING (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND classroom_web_owner_active())
 WITH CHECK (owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND classroom_web_owner_active());
CREATE FUNCTION classroom_web_revoke_qa_source(source uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $fn$
DECLARE changed bigint;
BEGIN
 PERFORM classroom_web_lock_owner();
 UPDATE qa_sources SET revoked_at=clock_timestamp()
   WHERE owner_id=nullif(current_setting('app.owner_id',true),'')::uuid AND id=source AND revoked_at IS NULL;
 GET DIAGNOSTICS changed=ROW_COUNT;
 RETURN changed=1;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_revoke_qa_source(uuid) FROM PUBLIC;
GRANT CREATE ON SCHEMA public TO classroom_web_qa_revoker;
ALTER FUNCTION classroom_web_revoke_qa_source(uuid) OWNER TO classroom_web_qa_revoker;
REVOKE CREATE ON SCHEMA public FROM classroom_web_qa_revoker;
GRANT EXECUTE ON FUNCTION classroom_web_revoke_qa_source(uuid) TO classroom_web_runtime;

CREATE FUNCTION classroom_web_guard_qa_item() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
DECLARE original qa_source_versions%ROWTYPE; selected uuid[];
BEGIN
 SELECT * INTO original FROM qa_source_versions WHERE owner_id=NEW.owner_id
   AND source_id=NEW.source_id AND id=NEW.revision_id;
 SELECT source_ids INTO selected FROM source_snapshots WHERE owner_id=NEW.owner_id AND id=NEW.snapshot_id;
 IF original.id IS NULL OR NOT NEW.source_id=ANY(selected)
   OR NEW.source_hash<>original.content_sha256 OR NEW.excerpt_end>char_length(original.text)
   OR NEW.frozen_text<>substring(original.text FROM NEW.excerpt_start+1 FOR NEW.excerpt_end-NEW.excerpt_start) THEN
   RAISE EXCEPTION 'snapshot excerpt must match its exact source version' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_qa_item() FROM PUBLIC;
CREATE TRIGGER qa_item_guard BEFORE INSERT ON source_items
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_item();

CREATE FUNCTION classroom_web_guard_qa_plan() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF (to_jsonb(NEW)-ARRAY['consumed_task_id','consumed_at'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['consumed_task_id','consumed_at'])
    OR OLD.consumed_task_id IS NOT NULL OR NEW.consumed_task_id IS NULL THEN
   RAISE EXCEPTION 'QA plan can be consumed once' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS (SELECT FROM tasks t WHERE t.owner_id=NEW.owner_id AND t.id=NEW.consumed_task_id
     AND t.plan_id=NEW.id AND t.class_session_id=NEW.class_session_id AND t.schema_version='pr07a-1') THEN
   RAISE EXCEPTION 'consumed task must belong to this plan and classroom' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_qa_plan() FROM PUBLIC;
CREATE TRIGGER qa_plan_guard BEFORE UPDATE ON generation_plans
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_plan();

CREATE FUNCTION classroom_web_guard_qa_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF NOT EXISTS (SELECT FROM class_sessions s WHERE s.owner_id=NEW.owner_id AND s.id=NEW.class_session_id
      AND s.course_material_version_id=NEW.course_material_version_id)
    OR EXISTS (SELECT FROM unnest(NEW.source_ids) x(id) WHERE NOT EXISTS (
      SELECT FROM qa_sources s WHERE s.owner_id=NEW.owner_id AND s.id=x.id AND s.class_session_id=NEW.class_session_id)) THEN
   RAISE EXCEPTION 'snapshot must use its classroom sources and material version' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_qa_snapshot() FROM PUBLIC;
CREATE TRIGGER qa_snapshot_guard BEFORE INSERT ON source_snapshots
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_snapshot();

CREATE FUNCTION classroom_web_guard_qa_question() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $fn$
BEGIN
 IF TG_OP='UPDATE' AND (
    (to_jsonb(NEW)-ARRAY['answer_markdown','cited_source_ids','understood','revision','updated_at'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['answer_markdown','cited_source_ids','understood','revision','updated_at'])
    OR NEW.revision<>OLD.revision+1
    OR (OLD.answer_markdown IS NOT NULL AND (NEW.answer_markdown IS DISTINCT FROM OLD.answer_markdown
      OR NEW.cited_source_ids IS DISTINCT FROM OLD.cited_source_ids))) THEN
   RAISE EXCEPTION 'question identity and published answer are immutable' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS (SELECT FROM tasks t JOIN generation_plans p ON p.owner_id=t.owner_id AND p.id=t.plan_id
     WHERE t.owner_id=NEW.owner_id AND t.id=NEW.task_id AND t.class_session_id=NEW.class_session_id
       AND p.snapshot_id=NEW.snapshot_id AND p.consumed_task_id=t.id) THEN
   RAISE EXCEPTION 'question must match its consumed task and frozen snapshot' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_qa_question() FROM PUBLIC;
CREATE TRIGGER qa_question_guard BEFORE INSERT OR UPDATE ON questions
 FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_qa_question();

ALTER TABLE tasks DROP CONSTRAINT ck_tasks_plan_not_implemented;
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_qa_plan FOREIGN KEY(owner_id,plan_id)
 REFERENCES generation_plans(owner_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_qa_plan_lineage FOREIGN KEY(owner_id,plan_id,class_session_id)
 REFERENCES generation_plans(owner_id,id,class_session_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE tasks DROP CONSTRAINT ck_tasks_schema_version;
ALTER TABLE tasks ADD CONSTRAINT ck_tasks_schema_version CHECK (schema_version IN ('1','pr07a-1'));
ALTER TABLE tasks ADD CONSTRAINT ck_tasks_qa_plan CHECK (
 (schema_version='pr07a-1' AND plan_id IS NOT NULL AND class_session_id IS NOT NULL AND kind='ai_question') OR
 (schema_version='1' AND plan_id IS NULL));
ALTER TABLE operation_receipts DROP CONSTRAINT ck_operation_receipts_resource_type;
ALTER TABLE operation_receipts ADD CONSTRAINT ck_operation_receipts_resource_type CHECK
 (resource_type IN ('task','semester','course','class_session','course_material_version','media_asset','media_upload','generation_plan','question'));
