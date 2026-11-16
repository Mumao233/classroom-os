CREATE TABLE media_assets (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    class_session_id uuid, kind text NOT NULL CHECK (kind IN ('slide','import_package','audio_chunk','export')),
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0), mime_type text NOT NULL,
    status text NOT NULL DEFAULT 'staging' CHECK (status IN ('staging','verified','committed','delete_pending','delete_failed','deleted')),
    expires_at timestamptz, confirmed_at timestamptz, access_revoked_at timestamptz, deleted_at timestamptz,
    first_cloud_received_at timestamptz, retention_anchor_at timestamptz, hard_expire_at timestamptz,
    retention_policy_version text NOT NULL DEFAULT 'unassigned',
    object_key text NOT NULL UNIQUE CHECK (object_key ~ '^media/[a-f0-9]{32}/[a-f0-9]{32}/[a-f0-9]{64}$'),
    object_etag text, object_version text,
    UNIQUE(owner_id,id),
    FOREIGN KEY(owner_id,class_session_id) REFERENCES class_sessions(owner_id,id) ON DELETE RESTRICT,
    CHECK (kind <> 'slide' OR class_session_id IS NOT NULL),
    CHECK (status NOT IN ('verified','committed') OR (object_etag IS NOT NULL AND first_cloud_received_at IS NOT NULL)),
    CHECK (status <> 'committed' OR confirmed_at IS NOT NULL),
    -- PR-04b must install an approved retention-policy authority before audio commit is enabled.
    CONSTRAINT media_audio_commit_not_enabled CHECK (kind <> 'audio_chunk' OR status <> 'committed')
);
CREATE TABLE media_uploads (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT, asset_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(), expires_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'staging' CHECK (state IN ('staging','uploaded','verified','rejected')),
    UNIQUE(owner_id,id), UNIQUE(owner_id,asset_id),
    FOREIGN KEY(owner_id,asset_id) REFERENCES media_assets(owner_id,id) ON DELETE RESTRICT
);
CREATE TABLE media_tickets (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT, asset_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(), expires_at timestamptz NOT NULL,
    session_id uuid NOT NULL, security_epoch bigint NOT NULL,
    asset_revision bigint NOT NULL, purpose text NOT NULL CHECK (purpose='download'),
    token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    FOREIGN KEY(owner_id,asset_id) REFERENCES media_assets(owner_id,id) ON DELETE RESTRICT
);
CREATE INDEX ix_media_upload_scan ON media_uploads(owner_id,state,expires_at);
CREATE INDEX ix_media_ticket_expiry ON media_tickets(owner_id,expires_at);
CREATE INDEX ix_media_asset_class_kind ON media_assets(owner_id,class_session_id,kind);
CREATE INDEX ix_media_asset_expiry ON media_assets(status,expires_at);

DO $body$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['media_assets','media_uploads','media_tickets'] LOOP
    EXECUTE format('REVOKE ALL ON TABLE %I FROM PUBLIC',t);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY owner_isolation ON %I TO classroom_web_runtime
      USING (owner_id = nullif(current_setting(''app.owner_id'',true),'''')::uuid AND public.classroom_web_owner_active())
      WITH CHECK (owner_id = nullif(current_setting(''app.owner_id'',true),'''')::uuid AND public.classroom_web_owner_active())',t);
  END LOOP;
END $body$;
GRANT SELECT,INSERT,UPDATE ON media_assets,media_uploads TO classroom_web_runtime;
GRANT SELECT,INSERT ON media_tickets TO classroom_web_runtime;

ALTER TABLE operation_receipts DROP CONSTRAINT ck_operation_receipts_resource_type;
ALTER TABLE operation_receipts ADD CONSTRAINT ck_operation_receipts_resource_type
  CHECK(resource_type IN ('task','semester','course','class_session','course_material_version','media_asset','media_upload'));

-- Immutable object identity prevents another upload from reusing an authorized asset.
CREATE FUNCTION classroom_web_media_identity() RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
  IF (NEW.owner_id,NEW.id,NEW.class_session_id,NEW.kind,NEW.sha256,NEW.size_bytes,NEW.mime_type,NEW.object_key)
     IS DISTINCT FROM
     (OLD.owner_id,OLD.id,OLD.class_session_id,OLD.kind,OLD.sha256,OLD.size_bytes,OLD.mime_type,OLD.object_key)
     OR (OLD.object_etag IS NOT NULL AND (NEW.object_etag,NEW.object_version) IS DISTINCT FROM (OLD.object_etag,OLD.object_version)) THEN
    RAISE EXCEPTION 'immutable media identity' USING ERRCODE='23514';
  END IF;
  IF (OLD.status <> 'staging' AND NEW.status='staging')
     OR (OLD.status='committed' AND NEW.status='verified')
     OR (OLD.status IN ('delete_pending','delete_failed','deleted') AND NEW.status IN ('verified','committed'))
     OR (OLD.access_revoked_at IS NOT NULL AND NEW.access_revoked_at IS DISTINCT FROM OLD.access_revoked_at)
     OR (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at)
     OR (OLD.expires_at IS NOT NULL AND (NEW.expires_at IS NULL OR NEW.expires_at>OLD.expires_at))
     OR (OLD.hard_expire_at IS NOT NULL AND (NEW.hard_expire_at IS NULL OR NEW.hard_expire_at>OLD.hard_expire_at))
     OR (OLD.first_cloud_received_at IS NOT NULL AND NEW.first_cloud_received_at IS DISTINCT FROM OLD.first_cloud_received_at) THEN
    RAISE EXCEPTION 'media revocation is irreversible' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $body$;
REVOKE ALL ON FUNCTION classroom_web_media_identity() FROM PUBLIC;
CREATE TRIGGER media_identity BEFORE UPDATE ON media_assets FOR EACH ROW EXECUTE FUNCTION classroom_web_media_identity();
