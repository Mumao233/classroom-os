"""Security/lineage DDL for the PR-02a schema.

The initial migration freezes its original SQL. The reviewed tombstone guard is
applied by additive revision 0002; it matches this current authoring source.
Editing this module cannot retroactively change an already applied migration.
"""

SECURITY_DDL = r"""
DO $role$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'classroom_web_runtime') THEN
    CREATE ROLE classroom_web_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS;
  ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname = 'classroom_web_runtime'
      AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin OR rolreplication)) THEN
    RAISE EXCEPTION 'classroom_web_runtime must be an unprivileged NOLOGIN group';
  END IF;
  IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
      WHERE r.rolname = 'classroom_web_runtime') THEN
    RAISE EXCEPTION 'classroom_web_runtime must not inherit other roles';
  END IF;
END
$role$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO classroom_web_runtime;
REVOKE ALL ON TABLE users FROM classroom_web_runtime;
REVOKE ALL ON TABLE users FROM PUBLIC;

CREATE FUNCTION classroom_web_owner_active() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
  SELECT EXISTS (SELECT FROM public.users
    WHERE id = nullif(current_setting('app.owner_id', true), '')::uuid
      AND state = 'active' AND deleted_at IS NULL)
$fn$;
REVOKE ALL ON FUNCTION classroom_web_owner_active() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_owner_active() TO classroom_web_runtime;

CREATE FUNCTION classroom_web_lock_owner() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE principal uuid := nullif(current_setting('app.owner_id', true), '')::uuid;
BEGIN
  IF principal IS NULL THEN
    RAISE EXCEPTION 'owner context required' USING ERRCODE = '42501';
  END IF;
  PERFORM 1 FROM public.users WHERE id = principal AND state = 'active'
    AND deleted_at IS NULL FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'owner unavailable' USING ERRCODE = '42501';
  END IF;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_lock_owner() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_lock_owner() TO classroom_web_runtime;

CREATE FUNCTION classroom_web_tombstoned(kind text, resource uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = public, pg_temp AS $fn$
  SELECT EXISTS (SELECT FROM public.deletion_tombstones
    WHERE owner_id = nullif(current_setting('app.owner_id', true), '')::uuid
      AND ((resource_type = kind AND resource_id = resource)
        OR (resource_type = 'account' AND resource_id = owner_id)))
$fn$;
REVOKE ALL ON FUNCTION classroom_web_tombstoned(text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION classroom_web_tombstoned(text, uuid) TO classroom_web_runtime;

CREATE FUNCTION classroom_web_guard_resource() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE
  kind text := TG_ARGV[0];
  deleting boolean := false;
  parent_semester uuid;
  material_course uuid;
BEGIN
  PERFORM public.classroom_web_lock_owner();
  IF NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid THEN
    RAISE EXCEPTION 'owner mismatch' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'resource identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.deleted_at IS NOT NULL OR NEW.revision <> OLD.revision + 1 THEN
      RAISE EXCEPTION 'deleted resource or invalid revision transition' USING ERRCODE = '23514';
    END IF;
    deleting := NEW.deleted_at IS NOT NULL;
    IF deleting AND (to_jsonb(NEW) - ARRAY['revision','updated_at','deleted_at'])
        IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['revision','updated_at','deleted_at']) THEN
      RAISE EXCEPTION 'deletion cannot mutate content' USING ERRCODE = '23514';
    END IF;
    IF kind = 'class_session' THEN
      IF NEW.course_material_version_id IS DISTINCT FROM OLD.course_material_version_id THEN
        RAISE EXCEPTION 'class material snapshot is immutable' USING ERRCODE = '23514';
      END IF;
    END IF;
  ELSIF NEW.deleted_at IS NOT NULL OR NEW.revision <> 1 THEN
    RAISE EXCEPTION 'new resource must be live revision one' USING ERRCODE = '23514';
  END IF;

  IF deleting THEN
    RETURN NEW;
  END IF;
  IF public.classroom_web_tombstoned(kind, NEW.id) THEN
    RAISE EXCEPTION 'resource is tombstoned' USING ERRCODE = '23514';
  END IF;

  -- Validate the old lineage as well as the destination: moving a child must
  -- never make content accessible again after an ancestor has been revoked.
  IF TG_OP = 'UPDATE' THEN
    IF kind = 'course' THEN
      PERFORM 1 FROM public.semesters WHERE owner_id = OLD.owner_id AND id = OLD.semester_id
        AND deleted_at IS NULL FOR SHARE;
      IF NOT FOUND OR public.classroom_web_tombstoned('semester', OLD.semester_id) THEN
        RAISE EXCEPTION 'old parent unavailable' USING ERRCODE = '23514';
      END IF;
    ELSIF kind = 'class_session' THEN
      SELECT semester_id INTO parent_semester FROM public.courses
        WHERE owner_id = OLD.owner_id AND id = OLD.course_id;
      PERFORM 1 FROM public.semesters WHERE owner_id = OLD.owner_id AND id = parent_semester
        AND deleted_at IS NULL FOR SHARE;
      IF NOT FOUND OR public.classroom_web_tombstoned('semester', parent_semester) THEN
        RAISE EXCEPTION 'old ancestor unavailable' USING ERRCODE = '23514';
      END IF;
      PERFORM 1 FROM public.courses WHERE owner_id = OLD.owner_id AND id = OLD.course_id
        AND semester_id = parent_semester AND deleted_at IS NULL FOR SHARE;
      IF NOT FOUND OR public.classroom_web_tombstoned('course', OLD.course_id) THEN
        RAISE EXCEPTION 'old parent unavailable' USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;

  IF kind = 'course' THEN
    PERFORM 1 FROM public.semesters WHERE owner_id = NEW.owner_id AND id = NEW.semester_id
      AND deleted_at IS NULL FOR SHARE;
    IF NOT FOUND OR public.classroom_web_tombstoned('semester', NEW.semester_id) THEN
      RAISE EXCEPTION 'parent unavailable' USING ERRCODE = '23514';
    END IF;
  ELSIF kind = 'class_session' THEN
    SELECT semester_id INTO parent_semester FROM public.courses
      WHERE owner_id = NEW.owner_id AND id = NEW.course_id;
    PERFORM 1 FROM public.semesters WHERE owner_id = NEW.owner_id AND id = parent_semester
      AND deleted_at IS NULL FOR SHARE;
    IF NOT FOUND OR public.classroom_web_tombstoned('semester', parent_semester) THEN
      RAISE EXCEPTION 'ancestor unavailable' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM public.courses WHERE owner_id = NEW.owner_id AND id = NEW.course_id
      AND semester_id = parent_semester AND deleted_at IS NULL FOR SHARE;
    IF NOT FOUND OR public.classroom_web_tombstoned('course', NEW.course_id) THEN
      RAISE EXCEPTION 'parent unavailable' USING ERRCODE = '23514';
    END IF;
    SELECT course_id INTO material_course FROM public.course_material_versions
      WHERE owner_id = NEW.owner_id AND id = NEW.course_material_version_id;
    IF NOT FOUND OR public.classroom_web_tombstoned('course_material_version', NEW.course_material_version_id)
        OR (TG_OP = 'INSERT' AND material_course IS DISTINCT FROM NEW.course_id) THEN
      RAISE EXCEPTION 'invalid material lineage' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_resource() FROM PUBLIC;

CREATE TRIGGER guard_semester BEFORE INSERT OR UPDATE ON semesters
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_resource('semester');
CREATE TRIGGER guard_course BEFORE INSERT OR UPDATE ON courses
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_resource('course');
CREATE TRIGGER guard_class BEFORE INSERT OR UPDATE ON class_sessions
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_resource('class_session');

CREATE FUNCTION classroom_web_guard_material() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE parent_semester uuid;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'material version is immutable' USING ERRCODE = '23514';
  END IF;
  PERFORM public.classroom_web_lock_owner();
  IF NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid
      OR public.classroom_web_tombstoned('course_material_version', NEW.id) THEN
    RAISE EXCEPTION 'owner mismatch or tombstoned material' USING ERRCODE = '23514';
  END IF;
  SELECT semester_id INTO parent_semester FROM public.courses
    WHERE owner_id = NEW.owner_id AND id = NEW.course_id;
  PERFORM 1 FROM public.semesters WHERE owner_id = NEW.owner_id AND id = parent_semester
    AND deleted_at IS NULL FOR SHARE;
  IF NOT FOUND OR public.classroom_web_tombstoned('semester', parent_semester) THEN
    RAISE EXCEPTION 'ancestor unavailable' USING ERRCODE = '23514';
  END IF;
  PERFORM 1 FROM public.courses WHERE owner_id = NEW.owner_id AND id = NEW.course_id
    AND semester_id = parent_semester AND deleted_at IS NULL FOR SHARE;
  IF NOT FOUND OR public.classroom_web_tombstoned('course', NEW.course_id) THEN
    RAISE EXCEPTION 'parent unavailable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_material() FROM PUBLIC;
CREATE TRIGGER guard_material BEFORE INSERT OR UPDATE OR DELETE ON course_material_versions
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_material();

CREATE FUNCTION classroom_web_check_current_material() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE current_id uuid; current_owner uuid; is_deleted boolean;
BEGIN
  SELECT current_material_version_id, owner_id, deleted_at IS NOT NULL
    INTO current_id, current_owner, is_deleted FROM public.courses WHERE id = NEW.id;
  IF FOUND THEN
    IF current_id IS NULL THEN
      IF NOT is_deleted THEN
        RAISE EXCEPTION 'live course requires a material version' USING ERRCODE = '23514';
      END IF;
    ELSIF NOT EXISTS (SELECT FROM public.course_material_versions
        WHERE owner_id = current_owner AND id = current_id AND course_id = NEW.id)
        OR (NOT is_deleted AND public.classroom_web_tombstoned('course_material_version', current_id)) THEN
      RAISE EXCEPTION 'course must point to its own material version' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NULL;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_check_current_material() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER check_current_material AFTER INSERT OR UPDATE ON courses
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION classroom_web_check_current_material();

CREATE FUNCTION classroom_web_guard_deletion_request() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
BEGIN
  PERFORM public.classroom_web_lock_owner();
  IF NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid THEN
    RAISE EXCEPTION 'owner mismatch' USING ERRCODE = '42501';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.scope_type IS DISTINCT FROM OLD.scope_type
        OR NEW.scope_id IS DISTINCT FROM OLD.scope_id
        OR NEW.access_revoked_at IS DISTINCT FROM OLD.access_revoked_at THEN
      RAISE EXCEPTION 'deletion scope and identity are immutable' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_deletion_request() FROM PUBLIC;
CREATE TRIGGER guard_deletion_request BEFORE INSERT OR UPDATE ON deletion_requests
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_deletion_request();

CREATE FUNCTION classroom_web_guard_tombstone() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE material_parent uuid; parent_semester uuid;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'tombstone is immutable' USING ERRCODE = '23514';
  END IF;
  PERFORM public.classroom_web_lock_owner();
  IF NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.owner_id', true), '')::uuid THEN
    RAISE EXCEPTION 'owner mismatch' USING ERRCODE = '42501';
  END IF;
  IF NEW.resource_type = 'semester' THEN
    PERFORM 1 FROM public.semesters WHERE owner_id = NEW.owner_id AND id = NEW.resource_id FOR UPDATE;
  ELSIF NEW.resource_type = 'course' THEN
    PERFORM 1 FROM public.courses WHERE owner_id = NEW.owner_id AND id = NEW.resource_id FOR UPDATE;
  ELSIF NEW.resource_type = 'class_session' THEN
    PERFORM 1 FROM public.class_sessions WHERE owner_id = NEW.owner_id AND id = NEW.resource_id FOR UPDATE;
  ELSIF NEW.resource_type = 'course_material_version' THEN
    -- Immutable material identity needs no row lock. Coordinate on the course
    -- BEFORE the request lock: pointer updates already hold this course row.
    -- Lock order: owner -> semester -> course -> request -> tombstone/FKs.
    SELECT course_id INTO material_parent FROM public.course_material_versions
      WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'resource unavailable' USING ERRCODE = '23514';
    END IF;
    SELECT semester_id INTO parent_semester FROM public.courses
      WHERE owner_id = NEW.owner_id AND id = material_parent;
    PERFORM 1 FROM public.semesters
      WHERE owner_id = NEW.owner_id AND id = parent_semester FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'material ancestor unavailable' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM public.courses
      WHERE owner_id = NEW.owner_id AND id = material_parent
        AND semester_id = parent_semester FOR UPDATE;
  ELSE
    -- Account-wide tombstones need the dedicated identity lifecycle in PR-04b.
    RAISE EXCEPTION 'resource type has no revocation adapter yet' USING ERRCODE = '23514';
  END IF;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'resource unavailable' USING ERRCODE = '23514';
  END IF;
  PERFORM 1 FROM public.deletion_requests WHERE owner_id = NEW.owner_id AND id = NEW.request_id
    AND scope_type = NEW.resource_type AND scope_id = NEW.resource_id FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'tombstone does not match deletion request scope' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_guard_tombstone() FROM PUBLIC;
CREATE TRIGGER guard_tombstone BEFORE INSERT OR UPDATE OR DELETE ON deletion_tombstones
  FOR EACH ROW EXECUTE FUNCTION classroom_web_guard_tombstone();

CREATE FUNCTION classroom_web_check_revocation() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
DECLARE revoked boolean; final_revision bigint;
BEGIN
  IF NEW.resource_type = 'semester' THEN
    SELECT deleted_at IS NOT NULL, revision INTO revoked, final_revision FROM public.semesters WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
  ELSIF NEW.resource_type = 'course' THEN
    SELECT deleted_at IS NOT NULL, revision INTO revoked, final_revision FROM public.courses WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
  ELSIF NEW.resource_type = 'class_session' THEN
    SELECT deleted_at IS NOT NULL, revision INTO revoked, final_revision FROM public.class_sessions WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
  ELSE
    -- Material revocation is represented by the immutable tombstone alone.
    revoked := true;
    SELECT revision INTO final_revision FROM public.course_material_versions
      WHERE owner_id = NEW.owner_id AND id = NEW.resource_id;
    IF EXISTS (SELECT FROM public.courses WHERE owner_id = NEW.owner_id
        AND current_material_version_id = NEW.resource_id AND deleted_at IS NULL) THEN
      RAISE EXCEPTION 'live course cannot retain revoked current material' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF revoked IS DISTINCT FROM true OR NEW.deleted_revision IS DISTINCT FROM final_revision THEN
    RAISE EXCEPTION 'tombstone requires atomic resource revocation' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_check_revocation() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER check_revocation AFTER INSERT ON deletion_tombstones
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION classroom_web_check_revocation();

CREATE FUNCTION classroom_web_check_deleted() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $fn$
BEGIN
  IF NEW.deleted_at IS NOT NULL AND NOT EXISTS (SELECT FROM public.deletion_tombstones
      WHERE owner_id = NEW.owner_id AND resource_type = TG_ARGV[0] AND resource_id = NEW.id) THEN
    RAISE EXCEPTION 'resource revocation requires tombstone' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END
$fn$;
REVOKE ALL ON FUNCTION classroom_web_check_deleted() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER check_deleted_semester AFTER UPDATE ON semesters
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION classroom_web_check_deleted('semester');
CREATE CONSTRAINT TRIGGER check_deleted_course AFTER UPDATE ON courses
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION classroom_web_check_deleted('course');
CREATE CONSTRAINT TRIGGER check_deleted_class AFTER UPDATE ON class_sessions
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION classroom_web_check_deleted('class_session');
"""

OWNER_TABLES = (
    "semesters", "courses", "course_material_versions", "class_sessions",
    "deletion_requests", "deletion_tombstones",
)


def security_statements():
    """Return the fixed DDL plus simple per-table policies, for snapshot authoring."""
    yield SECURITY_DDL
    for table in OWNER_TABLES:
        yield f"REVOKE ALL ON TABLE {table} FROM PUBLIC;"
        yield f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"
        yield f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"
        yield f"""CREATE POLICY owner_isolation ON {table} TO classroom_web_runtime
            USING (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid
                   AND public.classroom_web_owner_active())
            WITH CHECK (owner_id = nullif(current_setting('app.owner_id', true), '')::uuid
                   AND public.classroom_web_owner_active());"""
        verbs = "SELECT, INSERT" if table in ("course_material_versions", "deletion_tombstones") else "SELECT, INSERT, UPDATE"
        yield f"GRANT {verbs} ON TABLE {table} TO classroom_web_runtime;"
