-- PR-05b. All configuration ships disabled and has no product timing defaults.
CREATE TABLE capture_policy (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    enabled boolean NOT NULL DEFAULT false,
    lease_seconds integer CHECK (lease_seconds BETWEEN 2 AND 300),
    recovery_seconds integer CHECK (recovery_seconds BETWEEN 1 AND 300),
    max_epoch_samples bigint CHECK (max_epoch_samples BETWEEN 1 AND 576000000),
    max_chunks integer CHECK (max_chunks BETWEEN 1 AND 50000),
    microphone_consent_version text CHECK (char_length(microphone_consent_version) BETWEEN 1 AND 64),
    recovery_consent_version text CHECK (char_length(recovery_consent_version) BETWEEN 1 AND 64),
    asr_consent_version text CHECK (char_length(asr_consent_version) BETWEEN 1 AND 64),
    retention_policy_version text REFERENCES retention_policies(version) ON DELETE RESTRICT,
    CHECK (NOT enabled OR (lease_seconds IS NOT NULL AND recovery_seconds IS NOT NULL
        AND max_epoch_samples IS NOT NULL AND max_chunks IS NOT NULL
        AND microphone_consent_version IS NOT NULL AND recovery_consent_version IS NOT NULL
        AND asr_consent_version IS NOT NULL AND retention_policy_version IS NOT NULL))
);
INSERT INTO capture_policy(id) VALUES(true);
REVOKE ALL ON capture_policy FROM PUBLIC;
GRANT SELECT, UPDATE(id) ON capture_policy TO classroom_web_runtime;

CREATE TABLE consent_records (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind text NOT NULL, class_session_id uuid, snapshot_id uuid, device_id uuid,
    destination_revision text, policy_version text NOT NULL, scope jsonb NOT NULL,
    accepted_at timestamptz NOT NULL, revoked_at timestamptz,
    UNIQUE(owner_id,id),
    FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,snapshot_id) REFERENCES source_snapshots(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,device_id) REFERENCES devices(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE capture_epochs (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    class_session_id uuid NOT NULL, device_id uuid NOT NULL, fence_token bigint NOT NULL CHECK(fence_token >= 1),
    timeline_offset_ms bigint NOT NULL CHECK(timeline_offset_ms >= 0), sample_rate integer NOT NULL DEFAULT 16000 CHECK(sample_rate=16000),
    wall_started_at timestamptz, wall_ended_at timestamptz,
    last_expected_seq bigint CHECK(last_expected_seq BETWEEN 0 AND 49999), end_sample bigint CHECK(end_sample >= 0),
    status text NOT NULL CHECK(status IN ('recording','paused','retired','ended')),
    UNIQUE(owner_id,id),
    FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,device_id) REFERENCES devices(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE capture_leases (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    class_session_id uuid NOT NULL, device_id uuid NOT NULL, capture_epoch_id uuid NOT NULL,
    fence_token bigint NOT NULL CHECK(fence_token>=1), expires_at timestamptz NOT NULL,
    renewed_at timestamptz NOT NULL, released_at timestamptz,
    UNIQUE(owner_id,id),
    FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,device_id) REFERENCES devices(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,capture_epoch_id) REFERENCES capture_epochs(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE capture_gaps (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    class_session_id uuid NOT NULL, capture_epoch_id uuid NOT NULL,
    start_sample bigint, end_sample bigint, wall_start timestamptz, wall_end timestamptz,
    kind text NOT NULL CHECK(kind IN ('pause','known_missing','unknown','overlap')), reason_code text NOT NULL,
    UNIQUE(owner_id,id),
    FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,capture_epoch_id) REFERENCES capture_epochs(owner_id,id) ON DELETE RESTRICT,
    CHECK(start_sample IS NULL OR start_sample>=0),
    CHECK(end_sample IS NULL OR end_sample>=start_sample)
);
CREATE TABLE audio_chunks (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    class_session_id uuid NOT NULL, capture_epoch_id uuid NOT NULL, asset_id uuid NOT NULL,
    seq bigint NOT NULL CHECK(seq BETWEEN 0 AND 49999), start_sample bigint NOT NULL CHECK(start_sample>=0),
    end_sample bigint NOT NULL CHECK(end_sample>start_sample), sha256 char(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
    ingest_mode text NOT NULL CHECK(ingest_mode IN ('live','recovery')), accepted_fence bigint NOT NULL,
    UNIQUE(owner_id,id), UNIQUE(owner_id,capture_epoch_id,seq), UNIQUE(owner_id,asset_id),
    FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,capture_epoch_id) REFERENCES capture_epochs(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,asset_id) REFERENCES media_assets(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX ix_audio_chunk_epoch_sample ON audio_chunks(owner_id,class_session_id,capture_epoch_id,start_sample);

-- Internal implementation state complements the unchanged frozen table columns.
CREATE TABLE capture_epoch_state (
    epoch_id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    microphone_consent_id uuid NOT NULL, recovery_consent_id uuid NOT NULL, asr_consent_id uuid NOT NULL,
    hard_expire_at timestamptz NOT NULL, retention_policy_version text NOT NULL REFERENCES retention_policies(version),
    manifest_sha256 char(64) CHECK(manifest_sha256 ~ '^[a-f0-9]{64}$'),
    last_local_seq bigint NOT NULL DEFAULT 0 CHECK(last_local_seq BETWEEN 0 AND 49999),
    last_sample bigint NOT NULL DEFAULT 0 CHECK(last_sample>=0),
    last_monotonic_ms bigint NOT NULL DEFAULT 0 CHECK(last_monotonic_ms>=0),
    FOREIGN KEY(owner_id,epoch_id) REFERENCES capture_epochs(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,microphone_consent_id) REFERENCES consent_records(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,recovery_consent_id) REFERENCES consent_records(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,asr_consent_id) REFERENCES consent_records(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE capture_recovery_grants (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    epoch_id uuid NOT NULL, session_id uuid NOT NULL, device_id uuid NOT NULL,
    security_epoch bigint NOT NULL, token_hash char(64) NOT NULL UNIQUE,
    manifest_sha256 char(64) NOT NULL, last_seq bigint NOT NULL, last_sample bigint NOT NULL,
    expires_at timestamptz NOT NULL, UNIQUE(owner_id,id),
    FOREIGN KEY(owner_id,epoch_id) REFERENCES capture_epochs(owner_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(owner_id,device_id) REFERENCES devices(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE audio_chunk_state (
    chunk_id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    receipt_id uuid NOT NULL UNIQUE, recovery_grant_id uuid,
    FOREIGN KEY(owner_id,recovery_grant_id) REFERENCES capture_recovery_grants(owner_id,id),
    FOREIGN KEY(owner_id,chunk_id) REFERENCES audio_chunks(owner_id,id) ON DELETE RESTRICT
);

DO $body$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['consent_records','capture_epochs','capture_leases','capture_gaps',
      'audio_chunks','capture_epoch_state','capture_recovery_grants','audio_chunk_state'] LOOP
    EXECUTE format('REVOKE ALL ON TABLE %I FROM PUBLIC',t);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY owner_isolation ON %I TO classroom_web_runtime
      USING (owner_id = nullif(current_setting(''app.owner_id'',true),'''')::uuid AND public.classroom_web_owner_active())
      WITH CHECK (owner_id = nullif(current_setting(''app.owner_id'',true),'''')::uuid AND public.classroom_web_owner_active())',t);
  END LOOP;
END $body$;
GRANT SELECT, INSERT, UPDATE ON capture_epochs,capture_leases,capture_epoch_state,consent_records,audio_chunk_state TO classroom_web_runtime;
GRANT SELECT, INSERT ON capture_gaps,audio_chunks,capture_recovery_grants TO classroom_web_runtime;
GRANT UPDATE(wall_end) ON capture_gaps TO classroom_web_runtime;
GRANT UPDATE(id) ON audio_chunks,capture_recovery_grants TO classroom_web_runtime;

CREATE FUNCTION classroom_web_capture_identity() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE old_value jsonb:=to_jsonb(OLD); new_value jsonb:=to_jsonb(NEW);
BEGIN
  IF TG_TABLE_NAME IN ('audio_chunks','capture_recovery_grants') THEN
    IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'immutable capture record' USING ERRCODE='23514';END IF;
  ELSIF TG_TABLE_NAME='capture_epochs' THEN
    IF (new_value-ARRAY['status','wall_ended_at','last_expected_seq','end_sample']) IS DISTINCT FROM
       (old_value-ARRAY['status','wall_ended_at','last_expected_seq','end_sample'])
       OR (OLD.status IN ('retired','ended') AND NEW.status NOT IN ('retired','ended'))
       OR (OLD.last_expected_seq IS NOT NULL AND NEW.last_expected_seq IS DISTINCT FROM OLD.last_expected_seq)
       OR (OLD.end_sample IS NOT NULL AND NEW.end_sample IS DISTINCT FROM OLD.end_sample) THEN
      RAISE EXCEPTION 'immutable capture epoch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='capture_leases' THEN
    IF NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.fence_token<OLD.fence_token
       OR (NEW.capture_epoch_id IS DISTINCT FROM OLD.capture_epoch_id AND NEW.fence_token<=OLD.fence_token) THEN
      RAISE EXCEPTION 'capture fence must increase' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='consent_records' THEN
    IF (new_value-'revoked_at') IS DISTINCT FROM (old_value-'revoked_at')
       OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
      RAISE EXCEPTION 'consent identity is immutable' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='capture_epoch_state' THEN
    IF (new_value-ARRAY['manifest_sha256','last_local_seq','last_sample','last_monotonic_ms']) IS DISTINCT FROM
       (old_value-ARRAY['manifest_sha256','last_local_seq','last_sample','last_monotonic_ms'])
       OR (OLD.manifest_sha256 IS NOT NULL AND NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256)
       OR NEW.last_local_seq<OLD.last_local_seq OR NEW.last_sample<OLD.last_sample OR NEW.last_monotonic_ms<OLD.last_monotonic_ms THEN
      RAISE EXCEPTION 'capture progress is monotonic' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION classroom_web_capture_identity() FROM PUBLIC;
CREATE TRIGGER capture_identity BEFORE UPDATE ON capture_epochs FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_identity();
CREATE TRIGGER capture_identity BEFORE UPDATE ON capture_leases FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_identity();
CREATE TRIGGER capture_identity BEFORE UPDATE ON audio_chunks FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_identity();
CREATE TRIGGER capture_identity BEFORE UPDATE ON capture_recovery_grants FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_identity();
CREATE TRIGGER capture_identity BEFORE UPDATE ON consent_records FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_identity();
CREATE TRIGGER capture_identity BEFORE UPDATE ON capture_epoch_state FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_identity();

CREATE FUNCTION classroom_web_capture_chunk_lineage() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  IF NOT EXISTS(SELECT FROM capture_epochs e JOIN media_assets a ON a.owner_id=e.owner_id
      WHERE e.owner_id=NEW.owner_id AND e.id=NEW.capture_epoch_id AND a.id=NEW.asset_id
      AND a.kind='audio_chunk' AND a.status='staging' AND a.class_session_id=e.class_session_id
      AND NEW.class_session_id=e.class_session_id AND NEW.accepted_fence=e.fence_token
      AND a.sha256=NEW.sha256 AND a.size_bytes=2*(NEW.end_sample-NEW.start_sample)
      AND a.size_bytes BETWEEN 2 AND 32000) THEN
    RAISE EXCEPTION 'audio chunk lineage mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION classroom_web_capture_chunk_lineage() FROM PUBLIC;
CREATE TRIGGER capture_chunk_lineage BEFORE INSERT ON audio_chunks FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_chunk_lineage();

CREATE FUNCTION classroom_web_capture_audio_commit() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE policy capture_policy%ROWTYPE; epoch capture_epochs%ROWTYPE; state capture_epoch_state%ROWTYPE;
BEGIN
  IF NEW.kind<>'audio_chunk' OR NEW.status<>'committed' THEN RETURN NEW;END IF;
  SELECT * INTO policy FROM capture_policy WHERE id;
  IF NOT policy.enabled OR NEW.retention_policy_version IS DISTINCT FROM policy.retention_policy_version THEN
    RAISE EXCEPTION 'audio commit policy disabled' USING ERRCODE='23514',CONSTRAINT='media_audio_commit_not_enabled';
  END IF;
  SELECT e.* INTO epoch FROM audio_chunks c JOIN capture_epochs e ON (e.owner_id,e.id)=(c.owner_id,c.capture_epoch_id)
    WHERE c.owner_id=NEW.owner_id AND c.asset_id=NEW.id AND c.class_session_id=NEW.class_session_id
    AND c.sha256=NEW.sha256 AND NEW.size_bytes=2*(c.end_sample-c.start_sample);
  IF NOT FOUND THEN RAISE EXCEPTION 'audio commit requires immutable chunk' USING ERRCODE='23514';END IF;
  SELECT * INTO state FROM capture_epoch_state WHERE owner_id=NEW.owner_id AND epoch_id=epoch.id;
  IF NOT FOUND OR state.hard_expire_at<=clock_timestamp() OR NEW.hard_expire_at>state.hard_expire_at
      OR NEW.retention_policy_version IS DISTINCT FROM state.retention_policy_version
      OR NEW.retention_policy_version='unassigned' OR NEW.expires_at IS NULL OR NEW.hard_expire_at IS NULL
      OR EXISTS(SELECT FROM deletion_requests WHERE owner_id=NEW.owner_id AND scope_type='audio' AND scope_id=NEW.class_session_id)
      OR EXISTS(SELECT FROM class_sessions s JOIN courses c ON (c.owner_id,c.id)=(s.owner_id,s.course_id)
          JOIN semesters t ON (t.owner_id,t.id)=(c.owner_id,c.semester_id)
          WHERE s.owner_id=NEW.owner_id AND s.id=NEW.class_session_id
          AND (s.deleted_at IS NOT NULL OR c.deleted_at IS NOT NULL OR t.deleted_at IS NOT NULL)) THEN
    RAISE EXCEPTION 'audio scope unavailable' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM consent_records r WHERE r.owner_id=NEW.owner_id AND r.revoked_at IS NULL
      AND r.class_session_id=NEW.class_session_id AND r.device_id=epoch.device_id
      AND r.scope=jsonb_build_object('capture_epoch_id',epoch.id::text)
      AND ((r.id=state.microphone_consent_id AND r.kind='microphone' AND r.policy_version=policy.microphone_consent_version)
        OR (r.id=state.recovery_consent_id AND r.kind='recovery' AND r.policy_version=policy.recovery_consent_version)
        OR (r.id=state.asr_consent_id AND r.kind='asr' AND r.policy_version=policy.asr_consent_version)))<>3 THEN
    RAISE EXCEPTION 'audio consent unavailable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $fn$;
REVOKE ALL ON FUNCTION classroom_web_capture_audio_commit() FROM PUBLIC;
ALTER TABLE media_assets DROP CONSTRAINT media_audio_commit_not_enabled;
CREATE TRIGGER capture_audio_commit BEFORE INSERT OR UPDATE ON media_assets FOR EACH ROW EXECUTE FUNCTION classroom_web_capture_audio_commit();

GRANT SELECT ON capture_policy TO classroom_web_session_writer;
CREATE OR REPLACE FUNCTION identity_api.manage_ws_ticket(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,identity_control,public,pg_temp AS $f$
DECLARE s jsonb;c public.auth_challenges%ROWTYPE;n timestamptz;until_at timestamptz;
BEGIN
  s:=identity_api.validate_session(p);IF s IS NULL THEN RETURN NULL;END IF;n:=clock_timestamp();
  IF p->>'action'='issue' THEN
    -- Capture/fence authority belongs to PR-04; never turn an auth ticket into
    -- permission to record or activate a provider.
    IF p->'scope'->>'channel'='events' THEN
      IF p->'scope'->>'class_session_id' IS NOT NULL OR p->'scope'->>'capture_epoch_id' IS NOT NULL
        OR p->'scope'->>'fence_token' IS NOT NULL THEN RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
    ELSIF p->'scope'->>'channel'='capture' THEN
      IF NOT EXISTS(SELECT FROM public.capture_policy WHERE id AND enabled)
        OR p->'scope'->>'class_session_id' IS NULL OR p->'scope'->>'capture_epoch_id' IS NULL
        OR coalesce((p->'scope'->>'fence_token')::bigint,0)<1 THEN
        RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
    ELSE RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
    until_at:=least(n+interval '30 seconds',(s->>'expires_at')::timestamptz);
    INSERT INTO public.auth_challenges(id,kind,owner_id,token_hash,parent_hash,security_epoch,expires_at,scope)
      VALUES((p->>'id')::uuid,'ws',(s->>'owner_id')::uuid,p->>'ticket_hash',p->>'token_hash',(s->>'epoch')::bigint,until_at,p->'scope');
    RETURN jsonb_build_object('expires_at',until_at);
  ELSIF p->>'action'='consume' THEN
    SELECT * INTO c FROM public.auth_challenges WHERE kind='ws' AND token_hash=p->>'ticket_hash' FOR UPDATE;n:=clock_timestamp();
    IF c.id IS NULL OR c.consumed_at IS NOT NULL OR c.owner_id<>(s->>'owner_id')::uuid OR c.security_epoch<>(s->>'epoch')::bigint
      OR c.parent_hash IS DISTINCT FROM p->>'token_hash' OR n>=c.expires_at OR p->>'first_deadline' IS NULL OR n>=(p->>'first_deadline')::timestamptz
      OR c.scope->>'channel' IS DISTINCT FROM p->>'channel' THEN RETURN NULL;END IF;
    UPDATE public.auth_challenges SET consumed_at=n WHERE id=c.id;
    RETURN jsonb_build_object('owner_id',s->>'owner_id','scope',c.scope);
  ELSE RETURN NULL;END IF;
END $f$;
