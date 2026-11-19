-- Apply after the verified authorization_batch_three.sql overlay in a single
-- transaction in the same isolated POC database. No operational rows change.
DO $migration$
BEGIN
  IF NOT pg_try_advisory_xact_lock(6206,1) THEN
    RAISE EXCEPTION 'PR06A_BATCH_MIGRATION_BUSY' USING ERRCODE='lock_not_available';
  END IF;
  LOCK TABLE pr06a_control.activation,pr06a_control.revocation,pr06a_control.batch
    IN ACCESS EXCLUSIVE MODE NOWAIT;
  IF pg_catalog.to_regprocedure('pr06a_control.check_activation(text)') IS NULL
    OR pg_catalog.to_regprocedure('pr06a_api.revoke(text)') IS NULL
    OR pg_catalog.to_regprocedure('pr06a_api.release_reconciled(text)') IS NULL
    OR NOT EXISTS (
      SELECT FROM pg_catalog.pg_constraint
      WHERE conrelid='pr06a_control.activation'::regclass
        AND conname='activation_registered_authorization_check'
    ) THEN
    RAISE EXCEPTION 'PR06A_B4_MIGRATION_OVERLAY_MISMATCH';
  END IF;
  IF EXISTS (SELECT FROM pr06a_control.batch WHERE NOT closed) THEN
    RAISE EXCEPTION 'PR06A_BATCH_MIGRATION_REQUIRES_CLOSED_BATCH';
  END IF;
END $migration$;

ALTER TABLE pr06a_control.activation
  DROP CONSTRAINT activation_registered_authorization_check,
  ADD CONSTRAINT activation_registered_authorization_check CHECK (
    (authorization_id='PR06A-REAL-POC-AUTH-20261118'
      AND source_sha256='233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777')
    OR (authorization_id='PR06A-REAL-POC-AUTH-20261119-B2'
      AND source_sha256='9a56d2ea65799d821b19120a080b00d51689bc9644dce013f04213e4169d8047'
      AND account_ref='PR06A-ACCT-ATTEST-20261118-R2')
    OR (authorization_id='PR06A-REAL-POC-AUTH-20261119-B3'
      AND source_sha256='613acf9ba5fca8033e41bcebb34a7b6b0ad307e116733326031ff46e7cab80c2'
      AND account_ref='PR06A-ACCT-ATTEST-20261118-R2')
    OR (authorization_id='PR06A-REAL-POC-AUTH-20261120-B4'
      AND source_sha256='405f31b2836c711436aebb7f8bfe62db609f3960923861f4426361bbda13aa9a'
      AND account_ref='PR06A-ACCT-ATTEST-20261118-R2')
  );

ALTER TABLE pr06a_control.revocation
  DROP CONSTRAINT revocation_registered_authorization_check,
  ADD CONSTRAINT revocation_registered_authorization_check CHECK (
    authorization_id IN ('PR06A-REAL-POC-AUTH-20261118',
      'PR06A-REAL-POC-AUTH-20261119-B2','PR06A-REAL-POC-AUTH-20261119-B3','PR06A-REAL-POC-AUTH-20261120-B4')
  );

-- Preserve the existing owner, grants and calling boundaries.
CREATE OR REPLACE FUNCTION pr06a_control.check_activation(p_authorization text) RETURNS pr06a_control.activation
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE a pr06a_control.activation%ROWTYPE; switches jsonb;
BEGIN
  SELECT * INTO STRICT a FROM pr06a_control.activation
    WHERE authorization_id=p_authorization FOR SHARE;
  switches:=billing_api.lock_switches()->'flags';
  IF NOT a.enabled OR a.revoked OR a.executor_login<>session_user
     OR EXISTS (SELECT FROM pr06a_control.revocation WHERE authorization_id=a.authorization_id)
     OR clock_timestamp()<'2026-11-18 00:00:00+08' OR clock_timestamp()>='2027-01-01 00:00:00+08'
     OR (a.authorization_id IN ('PR06A-REAL-POC-AUTH-20261119-B2','PR06A-REAL-POC-AUTH-20261119-B3')
         AND clock_timestamp()<'2026-11-19 00:00:00+08')
     OR (a.authorization_id='PR06A-REAL-POC-AUTH-20261120-B4'
         AND clock_timestamp()<'2026-11-20 00:00:00+08')
     OR switches->'billing' IS DISTINCT FROM 'false'::jsonb
     OR switches->'asr_streaming' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'PR06A_ACTIVATION_BLOCKED';
  END IF;
  IF a.authorization_id='PR06A-REAL-POC-AUTH-20261119-B3' AND (
    SELECT count(*) FROM pr06a_control.batch
      WHERE authorization_id IN ('PR06A-REAL-POC-AUTH-20261118','PR06A-REAL-POC-AUTH-20261119-B2')
        AND closed
  )<>2 THEN
    RAISE EXCEPTION 'PR06A_PRIOR_BATCHES_NOT_CLOSED';
  END IF;
  IF a.authorization_id='PR06A-REAL-POC-AUTH-20261120-B4' AND (
    SELECT count(*) FROM pr06a_control.batch
      WHERE authorization_id IN ('PR06A-REAL-POC-AUTH-20261118',
        'PR06A-REAL-POC-AUTH-20261119-B2','PR06A-REAL-POC-AUTH-20261119-B3')
        AND closed
  )<>3 THEN
    RAISE EXCEPTION 'PR06A_PRIOR_BATCHES_NOT_CLOSED';
  END IF;
  RETURN a;
END $f$;

-- Close telemetry is operational evidence, independent of recognition and metering.
CREATE TABLE pr06a_control.websocket_close (
  attempt uuid PRIMARY KEY REFERENCES pr06a_control.attempts(attempt),
  status text NOT NULL CHECK (status IN ('clean','timeout','abnormal','failed')),
  elapsed_ms integer NOT NULL CHECK (elapsed_ms>=0),
  received_code integer CHECK (received_code BETWEEN 1000 AND 4999),
  received_reason text CHECK (received_reason IN ('','normal closure','going away','task finished',
    'task-finished','connection closed','timeout','provider_reason_redacted')),
  sent_code integer CHECK (sent_code BETWEEN 1000 AND 4999),
  sent_reason text CHECK (sent_reason IN ('','normal closure','going away','task finished',
    'task-finished','connection closed','timeout','provider_reason_redacted')),
  initiator text CHECK (initiator IN ('client','server')),
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((received_code IS NULL)=(received_reason IS NULL)),
  CHECK ((sent_code IS NULL)=(sent_reason IS NULL))
);

CREATE FUNCTION pr06a_api.record_close(f uuid, p_attempt uuid, p_status text, p_elapsed integer,
    p_received integer, p_received_reason text, p_sent integer, p_sent_reason text, p_initiator text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  INSERT INTO pr06a_control.websocket_close(attempt,status,elapsed_ms,received_code,received_reason,
      sent_code,sent_reason,initiator)
    SELECT a.attempt,p_status,p_elapsed,p_received,p_received_reason,p_sent,p_sent_reason,p_initiator
    FROM pr06a_control.attempts a JOIN pr06a_control.batch b USING(authorization_id)
    JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b,f) AND c.executor_login=session_user
      AND a.attempt=p_attempt AND a.state='submitting';
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_CLOSE_AUDIT_BLOCKED'; END IF;
  IF p_status<>'clean' THEN
    -- Close the fence even if the executor's completion omits its stop code.
    -- complete() records the applicable stop code in the same transaction.
    UPDATE pr06a_control.batch b SET closed=true
      FROM pr06a_control.attempts a
      WHERE a.attempt=p_attempt AND b.authorization_id=a.authorization_id
        AND pr06a_control.owns_batch(b,f);
  END IF;
END $f$;

REVOKE ALL ON TABLE pr06a_control.websocket_close FROM PUBLIC,classroom_pr06a_executor,classroom_pr06a_reconciler;
REVOKE ALL ON FUNCTION pr06a_api.record_close(uuid,uuid,text,integer,integer,text,integer,text,text)
  FROM PUBLIC,classroom_pr06a_reconciler;
GRANT EXECUTE ON FUNCTION pr06a_api.record_close(uuid,uuid,text,integer,integer,text,integer,text,text)
  TO classroom_pr06a_executor;

DO $acl$
BEGIN
  IF EXISTS (
    SELECT FROM pg_catalog.pg_class r
      CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(r.relacl,acldefault('r',r.relowner))) acl
      WHERE r.oid='pr06a_control.websocket_close'::regclass AND acl.grantee<>r.relowner
  ) OR EXISTS (
    SELECT FROM pg_catalog.pg_proc p
      CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
      WHERE p.oid='pr06a_api.record_close(uuid,uuid,text,integer,integer,text,integer,text,text)'::regprocedure
        AND acl.grantee<>p.proowner
        AND (acl.grantee<>'classroom_pr06a_executor'::regrole OR acl.is_grantable)
  ) THEN
    RAISE EXCEPTION 'PR06A_CLOSE_AUDIT_ACL_UNEXPECTED';
  END IF;
END $acl$;
