-- Apply after the verified authorization_batches.sql overlay in a single
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
    OR pg_catalog.to_regprocedure('pr06a_api.release_reconciled(text)') IS NULL THEN
    RAISE EXCEPTION 'PR06A_B3_MIGRATION_OVERLAY_MISMATCH';
  END IF;
  IF EXISTS (SELECT FROM pr06a_control.batch WHERE NOT closed) THEN
    RAISE EXCEPTION 'PR06A_BATCH_MIGRATION_REQUIRES_CLOSED_BATCH';
  END IF;
END $migration$;

ALTER TABLE pr06a_control.activation
  DROP CONSTRAINT activation_authorization_record_check,
  ADD CONSTRAINT activation_registered_authorization_check CHECK (
    (authorization_id='PR06A-REAL-POC-AUTH-20261118'
      AND source_sha256='233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777')
    OR (authorization_id='PR06A-REAL-POC-AUTH-20261119-B2'
      AND source_sha256='9a56d2ea65799d821b19120a080b00d51689bc9644dce013f04213e4169d8047'
      AND account_ref='PR06A-ACCT-ATTEST-20261118-R2')
    OR (authorization_id='PR06A-REAL-POC-AUTH-20261119-B3'
      AND source_sha256='613acf9ba5fca8033e41bcebb34a7b6b0ad307e116733326031ff46e7cab80c2'
      AND account_ref='PR06A-ACCT-ATTEST-20261118-R2')
  );

ALTER TABLE pr06a_control.revocation
  DROP CONSTRAINT revocation_authorization_id_check,
  ADD CONSTRAINT revocation_registered_authorization_check CHECK (
    authorization_id IN ('PR06A-REAL-POC-AUTH-20261118',
      'PR06A-REAL-POC-AUTH-20261119-B2','PR06A-REAL-POC-AUTH-20261119-B3')
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
  RETURN a;
END $f$;
