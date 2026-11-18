-- Apply in one transaction after install.sql or to the verified adf9f79
-- overlay in the same isolated POC database. Operational rows stay in place.
SELECT pg_advisory_xact_lock(6206,1);
LOCK TABLE pr06a_control.activation,pr06a_control.revocation,pr06a_control.batch IN ACCESS EXCLUSIVE MODE;

DO $migration$
BEGIN
  IF pg_catalog.to_regprocedure('pr06a_api.retain_packet(uuid,uuid,jsonb)') IS NULL
    OR pg_catalog.to_regprocedure('pr06a_api.check_connection(uuid,uuid,boolean)') IS NULL
    OR NOT EXISTS (SELECT FROM pg_catalog.pg_attribute
      WHERE attrelid='pr06a_control.activation'::regclass AND attname='risk_acceptance_sha256' AND NOT attisdropped) THEN
    RAISE EXCEPTION 'PR06A_BATCH_MIGRATION_OVERLAY_MISMATCH';
  END IF;
  IF EXISTS (SELECT FROM pr06a_control.batch WHERE NOT closed) THEN
    RAISE EXCEPTION 'PR06A_BATCH_MIGRATION_REQUIRES_CLOSED_BATCH';
  END IF;
END $migration$;

ALTER TABLE pr06a_control.activation
  DROP CONSTRAINT activation_authorization_id_check,
  DROP CONSTRAINT activation_source_sha256_check,
  ADD CONSTRAINT activation_authorization_record_check CHECK (
    (authorization_id='PR06A-REAL-POC-AUTH-20261118'
      AND source_sha256='233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777')
    OR (authorization_id='PR06A-REAL-POC-AUTH-20261119-B2'
      AND source_sha256='9a56d2ea65799d821b19120a080b00d51689bc9644dce013f04213e4169d8047'
      AND account_ref='PR06A-ACCT-ATTEST-20261118-R2')
  );

ALTER TABLE pr06a_control.revocation
  DROP CONSTRAINT revocation_authorization_id_check,
  ADD CONSTRAINT revocation_authorization_id_check CHECK (
    authorization_id IN ('PR06A-REAL-POC-AUTH-20261118','PR06A-REAL-POC-AUTH-20261119-B2')
  );

CREATE FUNCTION pr06a_api.revoke(p_authorization text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  PERFORM pg_advisory_xact_lock(6206,1);
  INSERT INTO pr06a_control.revocation(authorization_id) VALUES(p_authorization)
    ON CONFLICT DO NOTHING;
  UPDATE pr06a_control.activation SET revoked=true WHERE authorization_id=p_authorization AND NOT revoked;
END $f$;

CREATE FUNCTION pr06a_control.check_activation(p_authorization text) RETURNS pr06a_control.activation
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE a pr06a_control.activation%ROWTYPE; switches jsonb;
BEGIN
  SELECT * INTO STRICT a FROM pr06a_control.activation
    WHERE authorization_id=p_authorization FOR SHARE;
  switches:=billing_api.lock_switches()->'flags';
  IF NOT a.enabled OR a.revoked OR a.executor_login<>session_user
     OR EXISTS (SELECT FROM pr06a_control.revocation WHERE authorization_id=a.authorization_id)
     OR clock_timestamp()<'2026-11-18 00:00:00+08' OR clock_timestamp()>='2027-01-01 00:00:00+08'
     OR (a.authorization_id='PR06A-REAL-POC-AUTH-20261119-B2' AND clock_timestamp()<'2026-11-19 00:00:00+08')
     OR switches->'billing' IS DISTINCT FROM 'false'::jsonb
     OR switches->'asr_streaming' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'PR06A_ACTIVATION_BLOCKED';
  END IF;
  RETURN a;
END $f$;

CREATE FUNCTION pr06a_control.check_budget(p_authorization text,cycle timestamptz,additional bigint) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b jsonb;
BEGIN
  b:=billing_api.lock_budget(cycle);
  PERFORM pr06a_control.check_activation(p_authorization);
  IF clock_timestamp()>=(b->>'period_end')::timestamptz
     OR (b->>'risk')::numeric+additional>(b->>'budget_microyuan')::numeric-(b->>'safety_microyuan')::numeric
     OR (b->>'risk')::numeric+additional>=(b->>'budget_microyuan')::numeric
     OR b->'pools'->>'asr' IS NULL
     OR coalesce((b->'pool_risk'->>'asr')::numeric,0)+additional>(b->'pools'->>'asr')::numeric THEN
    RAISE EXCEPTION 'PR06A_BUDGET_BLOCKED';
  END IF;
END $f$;

CREATE OR REPLACE FUNCTION pr06a_control.check_batch(f uuid) RETURNS pr06a_control.batch
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE id text; a pr06a_control.activation%ROWTYPE; b pr06a_control.batch%ROWTYPE;
BEGIN
  -- Identify the owner before acquiring the activation, batch and budget locks.
  SELECT b0.authorization_id INTO STRICT id FROM pr06a_control.batch b0
    WHERE pr06a_control.owns_batch(b0,f);
  a:=pr06a_control.check_activation(id);
  SELECT b0.* INTO STRICT b FROM pr06a_control.batch b0
    WHERE b0.authorization_id=id AND pr06a_control.owns_batch(b0,f) FOR UPDATE;
  PERFORM pr06a_control.check_budget(id,b.period_start,0);
  IF b.closed OR b.reviewed_sha<>a.reviewed_sha OR clock_timestamp()>=b.lease_until
     OR pr06a_control.observed_cost(b.authorization_id)>=b.authorized_microyuan THEN
    RAISE EXCEPTION 'PR06A_BATCH_BLOCKED';
  END IF;
  RETURN b;
END $f$;

CREATE OR REPLACE FUNCTION pr06a_api.open_batch(f uuid,p_identity jsonb,p_credential text,p_samples jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE a pr06a_control.activation%ROWTYPE; cycle timestamptz; t timestamptz; inventory jsonb;
BEGIN
  a:=pr06a_control.check_activation(p_identity->>'authorization');
  SELECT jsonb_agg(to_jsonb(s) ORDER BY slot) INTO inventory FROM pr06a_control.samples s;
  IF p_identity IS DISTINCT FROM jsonb_build_object('authorization',a.authorization_id,'source_sha256',a.source_sha256,
      'baseline',a.baseline,'reviewed_sha',a.reviewed_sha,'workspace',a.workspace,'key_id',a.key_id,
      'region',a.region,'model',a.model,'endpoint',a.endpoint,'account_ref',a.account_ref)
     OR p_credential IS DISTINCT FROM a.credential_sha256 OR p_samples IS DISTINCT FROM inventory THEN
    RAISE EXCEPTION 'PR06A_IDENTITY_BLOCKED';
  END IF;
  IF a.authorization_id='PR06A-REAL-POC-AUTH-20261119-B2' AND NOT EXISTS (
    SELECT FROM pr06a_control.batch WHERE authorization_id='PR06A-REAL-POC-AUTH-20261118' AND closed
  ) THEN
    RAISE EXCEPTION 'PR06A_FIRST_BATCH_NOT_CLOSED';
  END IF;
  t:=clock_timestamp();
  cycle:=date_trunc('month',t AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai';
  PERFORM pr06a_control.check_budget(a.authorization_id,cycle,1000000);
  t:=clock_timestamp();
  INSERT INTO pr06a_control.batch(authorization_id,fence,owner_pid,owner_since,reviewed_sha,period_start,opened_at,lease_until)
    VALUES(a.authorization_id,f,pg_backend_pid(),
      (SELECT backend_start FROM pg_catalog.pg_stat_activity WHERE pid=pg_backend_pid()),
      a.reviewed_sha,cycle,t,t+interval '30 seconds');
  PERFORM pr06a_control.alert_budget(cycle);
END $f$;

CREATE FUNCTION pr06a_api.release_reconciled(p_authorization text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  SELECT * INTO STRICT b FROM pr06a_control.batch WHERE authorization_id=p_authorization FOR UPDATE;
  PERFORM billing_api.lock_budget(b.period_start);
  IF (NOT b.closed AND clock_timestamp()<b.lease_until)
      OR EXISTS(SELECT FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id
        AND (gross_microyuan IS NULL OR reconciled_metering_revision IS DISTINCT FROM metering_revision)) THEN
    RAISE EXCEPTION 'PR06A_EXPOSURE_UNRESOLVED';
  END IF;
  UPDATE pr06a_control.batch SET held_microyuan=0,released=true,
    closed=true,stop_code=coalesce(stop_code,'reconciled_closed') WHERE authorization_id=b.authorization_id;
END $f$;

DROP FUNCTION pr06a_control.check_budget(timestamptz,bigint);
DROP FUNCTION pr06a_control.check_activation();
DROP FUNCTION pr06a_api.revoke();
DROP FUNCTION pr06a_api.release_reconciled();

REVOKE ALL ON FUNCTION pr06a_control.check_activation(text),
  pr06a_control.check_budget(text,timestamptz,bigint),
  pr06a_api.revoke(text),pr06a_api.release_reconciled(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pr06a_api.release_reconciled(text) TO classroom_pr06a_reconciler;
