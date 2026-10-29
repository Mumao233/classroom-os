-- PR-02a independent review: additive correction to the frozen initial schema.
-- No truncation: preexisting invalid semester values abort this transaction.
ALTER TABLE semesters ADD CONSTRAINT ck_semesters_name_length
  CHECK (char_length(name) BETWEEN 1 AND 100);
ALTER TABLE semesters ADD CONSTRAINT ck_semesters_academic_year_length
  CHECK (academic_year IS NULL OR char_length(academic_year) <= 30);

CREATE OR REPLACE FUNCTION classroom_web_guard_tombstone() RETURNS trigger
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
