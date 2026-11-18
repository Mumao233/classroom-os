-- Isolated PR-06a v1 overlay. Apply transactionally after the PR-11a
-- candidate billing schema in the one authoritative POC database.
DO $roles$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['classroom_pr06a_executor','classroom_pr06a_reconciler'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',r);
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname=r AND
        (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication))
        OR EXISTS (SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=r)) THEN
      RAISE EXCEPTION 'unsafe PR-06a group role';
    END IF;
  END LOOP;
END $roles$;

CREATE SCHEMA pr06a_control;
CREATE SCHEMA pr06a_api;
REVOKE ALL ON SCHEMA pr06a_control,pr06a_api FROM PUBLIC;
GRANT USAGE ON SCHEMA pr06a_api TO classroom_pr06a_executor,classroom_pr06a_reconciler;
GRANT USAGE ON SCHEMA pr06a_control TO classroom_billing_control;

CREATE TABLE pr06a_control.activation (
  authorization_id text PRIMARY KEY CHECK (authorization_id='PR06A-REAL-POC-AUTH-20261118'),
  policy_version text NOT NULL DEFAULT 'v1' CHECK (policy_version='v1'),
  signer text NOT NULL DEFAULT '林珈诚' CHECK (signer='林珈诚'),
  source_sha256 text NOT NULL CHECK (source_sha256='233859c7a90ea2ddb0b3b7069edb39b20a82a88cc889c363297d31e96809b777'),
  baseline text NOT NULL CHECK (baseline='6ecd364d20bbd06a25fa4654570e454a04807e07'),
  reviewed_sha text NOT NULL CHECK (reviewed_sha ~ '^[a-f0-9]{40}$' AND reviewed_sha<>baseline),
  review_receipt text NOT NULL CHECK (review_receipt ~ '^[a-f0-9]{64}$'),
  vmware_receipt text NOT NULL CHECK (vmware_receipt ~ '^[a-f0-9]{64}$'),
  approval_ref text NOT NULL CHECK (length(approval_ref) BETWEEN 1 AND 200),
  account_ref text NOT NULL CHECK (length(account_ref) BETWEEN 1 AND 200),
  workspace text NOT NULL CHECK (workspace='ws-bm2ftvz01aej1eof'),
  key_id text NOT NULL CHECK (key_id='8245225'),
  region text NOT NULL CHECK (region='cn-beijing'),
  model text NOT NULL CHECK (model='qwen-audio-3.1-asr-flash-streaming'),
  endpoint text NOT NULL CHECK (endpoint='wss://ws-bm2ftvz01aej1eof.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference'),
  credential_sha256 text NOT NULL CHECK (credential_sha256 ~ '^[a-f0-9]{64}$'),
  executor_login name NOT NULL,
  enabled boolean NOT NULL DEFAULT false,
  revoked boolean NOT NULL DEFAULT false
);

CREATE TABLE pr06a_control.revocation (
  authorization_id text PRIMARY KEY CHECK (authorization_id='PR06A-REAL-POC-AUTH-20261118'),
  revoked_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION pr06a_control.guard_activation_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  PERFORM pg_advisory_xact_lock(6206,1);
  IF EXISTS (SELECT FROM pr06a_control.revocation WHERE authorization_id=NEW.authorization_id) THEN
    RAISE EXCEPTION 'PR06A_AUTHORIZATION_REVOKED';
  END IF;
  RETURN NEW;
END $f$;
CREATE TRIGGER pr06a_activation_insert_guard BEFORE INSERT ON pr06a_control.activation
  FOR EACH ROW EXECUTE FUNCTION pr06a_control.guard_activation_insert();

CREATE FUNCTION pr06a_control.guard_activation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  IF NOT pg_try_advisory_xact_lock(6206,1) THEN
    RAISE EXCEPTION 'PR06A_UPDATE_DURING_DISPATCH';
  END IF;
  IF (to_jsonb(NEW)-ARRAY['enabled','revoked']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['enabled','revoked'])
      OR (OLD.revoked AND NOT NEW.revoked) THEN
    RAISE EXCEPTION 'PR06A_ACTIVATION_IMMUTABLE';
  END IF;
  RETURN NEW;
END $f$;
CREATE TRIGGER pr06a_activation_guard BEFORE UPDATE ON pr06a_control.activation
  FOR EACH ROW EXECUTE FUNCTION pr06a_control.guard_activation();

CREATE FUNCTION pr06a_api.revoke() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  PERFORM pg_advisory_xact_lock(6206,1);
  INSERT INTO pr06a_control.revocation(authorization_id) VALUES('PR06A-REAL-POC-AUTH-20261118')
    ON CONFLICT DO NOTHING;
  UPDATE pr06a_control.activation SET revoked=true WHERE NOT revoked;
END $f$;

CREATE FUNCTION pr06a_control.guard_budget_update() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  IF NOT pg_try_advisory_xact_lock(6206,1) THEN
    RAISE EXCEPTION 'PR06A_UPDATE_DURING_DISPATCH';
  END IF;
  RETURN NULL;
END $f$;
CREATE TRIGGER pr06a_switch_dispatch_guard BEFORE UPDATE ON billing_control.switches
  FOR EACH STATEMENT EXECUTE FUNCTION pr06a_control.guard_budget_update();
CREATE TRIGGER pr06a_budget_dispatch_guard BEFORE UPDATE ON billing_control.platform_budgets
  FOR EACH STATEMENT EXECUTE FUNCTION pr06a_control.guard_budget_update();

CREATE TABLE pr06a_control.samples (
  slot smallint PRIMARY KEY CHECK (slot BETWEEN 1 AND 3),
  name text NOT NULL UNIQUE, wav_hash text NOT NULL UNIQUE, pcm_hash text NOT NULL,
  samples integer NOT NULL CHECK (samples BETWEEN 1 AND 160000),
  send_samples integer NOT NULL CHECK (send_samples BETWEEN 1 AND samples),
  send_hash text NOT NULL
);
INSERT INTO pr06a_control.samples VALUES
  (1,'1_mandarin.wav','e0ec352274858132b04d52db85ed2047485e1e1537bcc9f47a6f88803f43a509','f8022f15370f16cb6752bd1c8edef9885261c78866312c587f9dd512404ea5c0',69888,69888,'f8022f15370f16cb6752bd1c8edef9885261c78866312c587f9dd512404ea5c0'),
  (2,'2_mixed.wav','d006c9c11e021e4e15bf934b71f7f88bd5ebb98d792c957bfa4ce279e8499a7b','2d2a37fbbf63e32d0245e1d996c2fe121d3c23b6674ad5e58cc18b17ab243db2',82176,82176,'2d2a37fbbf63e32d0245e1d996c2fe121d3c23b6674ad5e58cc18b17ab243db2'),
  (3,'3_long.wav','4f4e93eee0e6d036cd57b60f66ee87d7d4c14d74b627b19a054fa8f3e421a326','a27033e0bec1c74ff49d3e81259234733301404d3737008cc1983858713a1a37',137472,32000,'e8e7098ccede40dfd05964a6e3ee6b3b54bfddb66bfa531a0e13c2f8c6684c27');

CREATE TABLE pr06a_control.batch (
  authorization_id text PRIMARY KEY REFERENCES pr06a_control.activation(authorization_id),
  fence uuid NOT NULL UNIQUE,
  owner_pid integer NOT NULL,
  owner_since timestamptz NOT NULL,
  reviewed_sha text NOT NULL,
  period_start timestamptz NOT NULL REFERENCES billing_control.platform_budgets(period_start),
  opened_at timestamptz NOT NULL,
  lease_until timestamptz NOT NULL,
  authorized_microyuan bigint NOT NULL DEFAULT 1000000 CHECK (authorized_microyuan=1000000),
  released boolean NOT NULL DEFAULT false,
  held_microyuan bigint NOT NULL DEFAULT 1000000 CHECK (held_microyuan BETWEEN 0 AND authorized_microyuan),
  confirmed_microyuan numeric NOT NULL DEFAULT 0 CHECK (confirmed_microyuan>=0),
  overrun_microyuan numeric GENERATED ALWAYS AS (greatest(confirmed_microyuan-authorized_microyuan,0)) STORED,
  closed boolean NOT NULL DEFAULT false,
  stop_code text,
  -- The monetary authorization_id has no synthetic meter ceilings or price rules.
  input_token_limit bigint CHECK (input_token_limit IS NULL),
  output_token_limit bigint CHECK (output_token_limit IS NULL),
  minimum_microyuan bigint CHECK (minimum_microyuan IS NULL),
  rounding_rule text CHECK (rounding_rule IS NULL),
  failed_handshake_price text CHECK (failed_handshake_price IS NULL),
  no_audio_price text CHECK (no_audio_price IS NULL)
);
GRANT SELECT ON pr06a_control.batch TO classroom_billing_control;

CREATE TABLE pr06a_control.attempts (
  authorization_id text NOT NULL REFERENCES pr06a_control.batch(authorization_id),
  slot smallint NOT NULL REFERENCES pr06a_control.samples(slot),
  attempt uuid NOT NULL UNIQUE, submission_key uuid NOT NULL UNIQUE,
  task_id uuid NOT NULL UNIQUE, request_id text UNIQUE CHECK (length(request_id) BETWEEN 1 AND 200),
  opened_at timestamptz NOT NULL, lease_until timestamptz NOT NULL,
  quota_samples integer NOT NULL CHECK (quota_samples BETWEEN 1 AND 160000),
  allowed_samples integer NOT NULL CHECK (allowed_samples BETWEEN 1 AND quota_samples),
  wav_hash text NOT NULL, pcm_hash text NOT NULL, send_hash text NOT NULL,
  reserved_samples integer NOT NULL DEFAULT 0 CHECK (reserved_samples BETWEEN 0 AND allowed_samples),
  state text NOT NULL DEFAULT 'submitting' CHECK (state IN ('submitting','succeeded','failed','unknown')),
  usage_revision integer NOT NULL DEFAULT 0,
  input_tokens bigint CHECK (input_tokens>=0), output_tokens bigint CHECK (output_tokens>=0),
  usage_final boolean NOT NULL DEFAULT false,
  gross_microyuan bigint CHECK (gross_microyuan>=0),
  metering_revision integer NOT NULL DEFAULT 0,
  observed_input bigint CHECK (observed_input>=0),
  observed_output bigint CHECK (observed_output>=0),
  observed_total bigint CHECK (observed_total>=0),
  reconciled_metering_revision integer,
  PRIMARY KEY (authorization_id,slot)
);

CREATE TABLE pr06a_control.frames (
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  start_sample integer NOT NULL CHECK (start_sample>=0),
  end_sample integer NOT NULL CHECK (end_sample>start_sample AND end_sample-start_sample<=1600),
  frame_hash text NOT NULL CHECK (frame_hash ~ '^[a-f0-9]{64}$'),
  intended_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  disposition text NOT NULL DEFAULT 'intent' CHECK (disposition IN ('intent','confirmed','not_sent')),
  confirmed_at timestamptz,
  PRIMARY KEY (attempt,start_sample),
  UNIQUE (attempt,end_sample)
);

CREATE TABLE pr06a_control.usage (
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  revision integer NOT NULL,
  request_id text,
  input_tokens bigint NOT NULL CHECK (input_tokens>=0),
  output_tokens bigint NOT NULL CHECK (output_tokens>=0),
  final boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (attempt,revision)
);

CREATE TABLE pr06a_control.recognition (
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  sentence_id bigint NOT NULL,
  revision integer NOT NULL,
  begin_ms bigint NOT NULL,
  end_ms bigint,
  final boolean NOT NULL,
  transcript text NOT NULL,
  required_sample integer NOT NULL,
  PRIMARY KEY (attempt,sentence_id,revision),
  FOREIGN KEY (attempt,required_sample) REFERENCES pr06a_control.frames(attempt,end_sample)
);

CREATE TABLE pr06a_control.bill_evidence (
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  revision integer NOT NULL CHECK (revision>0),
  request_id text,
  input_tokens bigint NOT NULL CHECK (input_tokens>=0),
  output_tokens bigint NOT NULL CHECK (output_tokens>=0),
  gross_microyuan bigint NOT NULL CHECK (gross_microyuan>=0),
  net_microyuan bigint NOT NULL CHECK (net_microyuan>=0),
  bill_month text NOT NULL CHECK (bill_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
  evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[a-f0-9]{64}$'),
  reviewed_metering_revision integer NOT NULL CHECK (reviewed_metering_revision>=0),
  discrepancy_resolved boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (attempt,revision),
  CHECK (request_id IS NOT NULL OR (input_tokens=0 AND output_tokens=0 AND gross_microyuan=0 AND net_microyuan=0))
);

CREATE FUNCTION pr06a_control.risk_microyuan(p_input bigint, p_output bigint, p_total bigint) RETURNS numeric
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $f$
  SELECT 6::numeric*coalesce(p_input,0)+4.5::numeric*coalesce(p_output,0)
         +4.5::numeric*greatest(0,coalesce(p_total,0)::numeric-coalesce(p_input,0)-coalesce(p_output,0))
$f$;

-- Existing ordinary admission sees the specialized hold and actuals in
-- the same locked budget row. The production schema alone is unchanged.
GRANT SELECT (authorization_id, observed_input, observed_output, observed_total, gross_microyuan,
              reconciled_metering_revision, metering_revision,usage_final) ON pr06a_control.attempts TO classroom_billing_control;
CREATE VIEW billing_control.pr06a_combined_exposures AS
SELECT period_start,pool,held,unknown,settled FROM billing_control.exposures
UNION ALL
SELECT b.period_start,'asr',CASE WHEN NOT b.closed THEN b.held_microyuan ELSE 0 END,
  greatest(coalesce(u.unresolved,0),b.held_microyuan,
           CASE WHEN u.unmetered AND b.held_microyuan=0 THEN b.authorized_microyuan ELSE 0 END)
    - CASE WHEN NOT b.closed THEN b.held_microyuan ELSE 0 END,
  b.confirmed_microyuan
FROM pr06a_control.batch b
LEFT JOIN LATERAL (
  SELECT ceil(sum(greatest(0,pr06a_control.risk_microyuan(a.observed_input,a.observed_output,a.observed_total)
                                  -coalesce(a.gross_microyuan,0)))) AS unresolved,
         bool_or(NOT a.usage_final OR a.observed_input IS NULL OR a.observed_output IS NULL) AS unmetered
  FROM pr06a_control.attempts a WHERE a.authorization_id=b.authorization_id
    AND (a.gross_microyuan IS NULL OR a.reconciled_metering_revision IS DISTINCT FROM a.metering_revision)
) u ON true;
ALTER VIEW billing_control.pr06a_combined_exposures OWNER TO classroom_billing_control;

CREATE OR REPLACE FUNCTION billing_api.lock_budget(p_start timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control,pg_temp AS $f$
DECLARE b billing_control.platform_budgets%ROWTYPE; totals jsonb; pool_totals jsonb;
BEGIN
  SELECT * INTO STRICT b FROM billing_control.platform_budgets WHERE period_start=p_start FOR UPDATE;
  SELECT jsonb_build_object('held',coalesce(sum(held),0),'unknown',coalesce(sum(unknown),0),
    'settled',coalesce(sum(settled),0),'risk',b.fixed_microyuan+coalesce(sum(held+unknown+settled),0))
    INTO totals FROM billing_control.pr06a_combined_exposures WHERE period_start=p_start;
  SELECT coalesce(jsonb_object_agg(pool,risk),'{}'::jsonb) INTO pool_totals FROM
    (SELECT pool,sum(held+unknown+settled) risk FROM billing_control.pr06a_combined_exposures
      WHERE period_start=p_start GROUP BY pool) t;
  RETURN to_jsonb(b)||totals||jsonb_build_object('pool_risk',pool_totals);
END $f$;

CREATE FUNCTION pr06a_control.check_activation() RETURNS pr06a_control.activation
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE a pr06a_control.activation%ROWTYPE; switches jsonb;
BEGIN
  SELECT * INTO STRICT a FROM pr06a_control.activation FOR SHARE;
  switches:=billing_api.lock_switches()->'flags';
  IF NOT a.enabled OR a.revoked OR a.executor_login<>session_user
     OR EXISTS (SELECT FROM pr06a_control.revocation WHERE authorization_id=a.authorization_id)
     OR clock_timestamp()<'2026-11-18 00:00:00+08' OR clock_timestamp()>='2027-01-01 00:00:00+08'
     OR switches->'billing' IS DISTINCT FROM 'false'::jsonb
     OR switches->'asr_streaming' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'PR06A_ACTIVATION_BLOCKED';
  END IF;
  RETURN a;
END $f$;

CREATE FUNCTION pr06a_control.check_budget(cycle timestamptz, additional bigint) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b jsonb;
BEGIN
  b:=billing_api.lock_budget(cycle);
  PERFORM pr06a_control.check_activation();
  IF clock_timestamp()>=(b->>'period_end')::timestamptz
     OR (b->>'risk')::numeric+additional>(b->>'budget_microyuan')::numeric-(b->>'safety_microyuan')::numeric
     OR (b->>'risk')::numeric+additional>=(b->>'budget_microyuan')::numeric
     OR b->'pools'->>'asr' IS NULL
     OR coalesce((b->'pool_risk'->>'asr')::numeric,0)+additional>(b->'pools'->>'asr')::numeric THEN
    RAISE EXCEPTION 'PR06A_BUDGET_BLOCKED';
  END IF;
END $f$;

CREATE FUNCTION pr06a_control.observed_cost(id text) RETURNS numeric
LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $f$
  SELECT coalesce(sum(CASE WHEN a.gross_microyuan IS NOT NULL AND a.reconciled_metering_revision=a.metering_revision
       THEN a.gross_microyuan ELSE greatest(a.gross_microyuan,
          pr06a_control.risk_microyuan(a.observed_input,a.observed_output,a.observed_total)) END),0)
  FROM pr06a_control.attempts a WHERE a.authorization_id=id
$f$;

CREATE FUNCTION pr06a_control.owns_batch(b pr06a_control.batch, f uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $f$
  SELECT b.fence=f AND b.owner_pid=pg_backend_pid()
    AND b.owner_since=(SELECT backend_start FROM pg_catalog.pg_stat_activity WHERE pid=pg_backend_pid())
$f$;

CREATE FUNCTION pr06a_control.alert_budget(cycle timestamptz) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b jsonb;
BEGIN
  b:=billing_api.lock_budget(cycle);
  INSERT INTO billing_control.alerts(period_start,threshold)
    SELECT cycle,t FROM unnest(ARRAY[60,80,100]) t
    WHERE (b->>'risk')::numeric*100 >= (b->>'budget_microyuan')::numeric*t
    ON CONFLICT DO NOTHING;
END $f$;

CREATE FUNCTION pr06a_control.check_batch(f uuid) RETURNS pr06a_control.batch
LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $f$
DECLARE a pr06a_control.activation%ROWTYPE; b pr06a_control.batch%ROWTYPE;
BEGIN
  a:=pr06a_control.check_activation();
  SELECT b0.* INTO STRICT b FROM pr06a_control.batch b0 WHERE pr06a_control.owns_batch(b0,f) FOR UPDATE;
  PERFORM pr06a_control.check_budget(b.period_start,0);
  IF b.closed OR b.reviewed_sha<>a.reviewed_sha OR clock_timestamp()>=b.lease_until
     OR pr06a_control.observed_cost(b.authorization_id)>=b.authorized_microyuan THEN
    RAISE EXCEPTION 'PR06A_BATCH_BLOCKED';
  END IF;
  RETURN b;
END $f$;

CREATE FUNCTION pr06a_api.open_batch(f uuid, p_identity jsonb, p_credential text, p_samples jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE a pr06a_control.activation%ROWTYPE; cycle timestamptz; t timestamptz; inventory jsonb;
BEGIN
  a:=pr06a_control.check_activation();
  SELECT jsonb_agg(to_jsonb(s) ORDER BY slot) INTO inventory FROM pr06a_control.samples s;
  IF p_identity IS DISTINCT FROM jsonb_build_object('authorization',a.authorization_id,'source_sha256',a.source_sha256,
      'baseline',a.baseline,'reviewed_sha',a.reviewed_sha,'workspace',a.workspace,'key_id',a.key_id,
      'region',a.region,'model',a.model,'endpoint',a.endpoint,'account_ref',a.account_ref)
     OR p_credential IS DISTINCT FROM a.credential_sha256 OR p_samples IS DISTINCT FROM inventory THEN
    RAISE EXCEPTION 'PR06A_IDENTITY_BLOCKED';
  END IF;
  t:=clock_timestamp();
  cycle:=date_trunc('month',t AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai';
  PERFORM pr06a_control.check_budget(cycle,1000000);
  t:=clock_timestamp();
  INSERT INTO pr06a_control.batch(authorization_id,fence,owner_pid,owner_since,reviewed_sha,period_start,opened_at,lease_until)
    VALUES(a.authorization_id,f,pg_backend_pid(),
      (SELECT backend_start FROM pg_catalog.pg_stat_activity WHERE pid=pg_backend_pid()),
      a.reviewed_sha,cycle,t,t+interval '30 seconds');
  PERFORM pr06a_control.alert_budget(cycle);
END $f$;

CREATE FUNCTION pr06a_api.claim_attempt(f uuid, s integer, p_attempt uuid, p_submission uuid, p_task uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE; sample pr06a_control.samples%ROWTYPE; t timestamptz;
BEGIN
  b:=pr06a_control.check_batch(f);
  SELECT * INTO STRICT sample FROM pr06a_control.samples WHERE slot=s;
  IF s<>1+(SELECT count(*) FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id)
     OR EXISTS(SELECT FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id AND
       (state<>'succeeded' OR NOT usage_final OR reserved_samples<>allowed_samples)) THEN
    RAISE EXCEPTION 'PR06A_ATTEMPT_BLOCKED';
  END IF;
  t:=clock_timestamp();
  INSERT INTO pr06a_control.attempts(authorization_id,slot,attempt,submission_key,task_id,opened_at,lease_until,
      quota_samples,allowed_samples,wav_hash,pcm_hash,send_hash)
    VALUES(b.authorization_id,s,p_attempt,p_submission,p_task,t,least(t+interval '10 seconds',b.lease_until),
      sample.samples,sample.send_samples,sample.wav_hash,sample.pcm_hash,sample.send_hash);
END $f$;

CREATE FUNCTION pr06a_api.frame_intent(f uuid, p_attempt uuid, p_start integer, p_end integer, p_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE; a pr06a_control.attempts%ROWTYPE;
BEGIN
  b:=pr06a_control.check_batch(f);
  SELECT * INTO STRICT a FROM pr06a_control.attempts WHERE attempt=p_attempt AND authorization_id=b.authorization_id;
  IF a.state<>'submitting' OR clock_timestamp()>=a.lease_until
     OR p_start IS DISTINCT FROM a.reserved_samples OR p_end>a.allowed_samples
     OR EXISTS(SELECT FROM pr06a_control.frames WHERE attempt=a.attempt AND disposition<>'confirmed')
     OR p_end-p_start+(SELECT sum(reserved_samples) FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id)>480000 THEN
    RAISE EXCEPTION 'PR06A_FRAME_BLOCKED';
  END IF;
  INSERT INTO pr06a_control.frames(attempt,start_sample,end_sample,frame_hash)
    VALUES(a.attempt,p_start,p_end,p_hash);
  UPDATE pr06a_control.attempts SET reserved_samples=p_end WHERE attempt=a.attempt;
END $f$;

CREATE FUNCTION pr06a_api.lock_dispatch(f uuid, p_lock boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  IF p_lock THEN
    IF NOT EXISTS (SELECT FROM pr06a_control.batch b WHERE pr06a_control.owns_batch(b,f)) THEN
      RAISE EXCEPTION 'PR06A_DISPATCH_FENCE_BLOCKED';
    END IF;
    PERFORM pg_advisory_lock_shared(6206,1);
  ELSIF EXISTS (SELECT FROM pg_catalog.pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
      AND classid=6206 AND objid=1 AND objsubid=2 AND mode='ShareLock' AND granted) THEN
    PERFORM pg_advisory_unlock_shared(6206,1);
  END IF;
END $f$;

CREATE FUNCTION pr06a_control.dispatch_seconds(b pr06a_control.batch, p_attempt uuid) RETURNS double precision
LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $f$
  SELECT extract(epoch FROM least(a.lease_until,b.lease_until,budget.period_end,
      '2027-01-01 00:00:00+08'::timestamptz) - clock_timestamp())::double precision
  FROM pr06a_control.attempts a JOIN billing_control.platform_budgets budget ON budget.period_start=b.period_start
  WHERE a.attempt=p_attempt AND a.authorization_id=b.authorization_id
    AND EXISTS (SELECT FROM pg_catalog.pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
      AND classid=6206 AND objid=1 AND objsubid=2 AND mode='ShareLock' AND granted)
$f$;

CREATE FUNCTION pr06a_api.check_frame(f uuid, p_attempt uuid, p_start integer) RETURNS double precision
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  b:=pr06a_control.check_batch(f);
  IF NOT EXISTS (SELECT FROM pr06a_control.attempts a JOIN pr06a_control.frames fr USING(attempt)
      WHERE a.authorization_id=b.authorization_id AND a.attempt=p_attempt AND a.state='submitting'
        AND clock_timestamp()<a.lease_until AND fr.start_sample=p_start AND fr.disposition='intent') THEN
    RAISE EXCEPTION 'PR06A_FRAME_BLOCKED';
  END IF;
  RETURN pr06a_control.dispatch_seconds(b,p_attempt);
END $f$;

CREATE FUNCTION pr06a_api.confirm_frame(f uuid, p_attempt uuid, p_start integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  UPDATE pr06a_control.frames fr SET disposition='confirmed',confirmed_at=clock_timestamp()
    FROM pr06a_control.attempts a JOIN pr06a_control.batch b USING(authorization_id)
    JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b,f) AND c.executor_login=session_user AND a.attempt=p_attempt AND fr.attempt=a.attempt
      AND fr.start_sample=p_start AND fr.disposition='intent';
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_FRAME_UNCONFIRMED'; END IF;
END $f$;

CREATE FUNCTION pr06a_api.discard_frame(f uuid, p_attempt uuid, p_start integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  UPDATE pr06a_control.frames fr SET disposition='not_sent'
    FROM pr06a_control.attempts a JOIN pr06a_control.batch b USING(authorization_id)
    JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b,f) AND c.executor_login=session_user AND a.attempt=p_attempt AND fr.attempt=a.attempt
      AND fr.start_sample=p_start AND fr.disposition='intent';
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_FRAME_DISCARD_BLOCKED'; END IF;
END $f$;

CREATE FUNCTION pr06a_api.observe(f uuid, p_attempt uuid, p_request text, p_in bigint, p_out bigint, p_final boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE; a pr06a_control.attempts%ROWTYPE; rev integer;
BEGIN
  SELECT b0.* INTO STRICT b FROM pr06a_control.batch b0 JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b0,f) AND c.executor_login=session_user FOR UPDATE OF b0;
  SELECT * INTO STRICT a FROM pr06a_control.attempts WHERE attempt=p_attempt AND authorization_id=b.authorization_id FOR UPDATE;
  PERFORM billing_api.lock_budget(b.period_start);
  IF (p_request IS NOT NULL AND a.request_id IS NOT NULL AND p_request<>a.request_id)
     OR ((p_in IS NULL)<>(p_out IS NULL))
     OR (p_in IS NOT NULL AND (p_in<coalesce(a.input_tokens,0) OR p_out<coalesce(a.output_tokens,0)
          OR a.usage_final))
     OR (p_final AND (p_in IS NULL OR coalesce(p_request,a.request_id) IS NULL)) THEN
    RAISE EXCEPTION 'PR06A_USAGE_CONFLICT';
  END IF;
  UPDATE pr06a_control.attempts SET request_id=coalesce(p_request,a.request_id),
    metering_revision=metering_revision+CASE WHEN a.request_id IS NULL AND p_request IS NOT NULL THEN 1 ELSE 0 END
    WHERE attempt=a.attempt;
  IF p_in IS NOT NULL AND (p_in IS DISTINCT FROM a.input_tokens OR p_out IS DISTINCT FROM a.output_tokens OR p_final) THEN
    rev:=a.usage_revision+1;
    INSERT INTO pr06a_control.usage(attempt,revision,request_id,input_tokens,output_tokens,final)
      VALUES(a.attempt,rev,coalesce(p_request,a.request_id),p_in,p_out,p_final);
    UPDATE pr06a_control.attempts SET input_tokens=p_in,output_tokens=p_out,usage_final=p_final,usage_revision=rev,
      metering_revision=metering_revision+1,observed_input=greatest(observed_input,p_in),
      observed_output=greatest(observed_output,p_out) WHERE attempt=a.attempt;
  END IF;
  PERFORM pr06a_control.alert_budget(b.period_start);
  RETURN pr06a_control.observed_cost(b.authorization_id)>=b.authorized_microyuan;
END $f$;

CREATE FUNCTION pr06a_api.observe_risk(f uuid, p_attempt uuid, p_in bigint, p_out bigint, p_total bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  SELECT b0.* INTO STRICT b FROM pr06a_control.batch b0 JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b0,f) AND c.executor_login=session_user FOR UPDATE OF b0;
  PERFORM billing_api.lock_budget(b.period_start);
  UPDATE pr06a_control.attempts SET
    observed_input=greatest(observed_input,p_in),observed_output=greatest(observed_output,p_out),
    observed_total=greatest(observed_total,p_total),
    metering_revision=metering_revision+CASE WHEN
        greatest(observed_input,p_in) IS DISTINCT FROM observed_input
        OR greatest(observed_output,p_out) IS DISTINCT FROM observed_output
        OR greatest(observed_total,p_total) IS DISTINCT FROM observed_total THEN 1 ELSE 0 END
    WHERE attempt=p_attempt AND authorization_id=b.authorization_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_RISK_EVIDENCE_BLOCKED'; END IF;
  PERFORM pr06a_control.alert_budget(b.period_start);
END $f$;

CREATE FUNCTION pr06a_api.record_sentence(f uuid, p_attempt uuid, p_id bigint, p_revision integer,
    p_begin bigint, p_end bigint, p_final boolean, p_text text, p_required integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  INSERT INTO pr06a_control.recognition
    SELECT a.attempt,p_id,p_revision,p_begin,p_end,p_final,p_text,p_required
    FROM pr06a_control.attempts a JOIN pr06a_control.batch b USING(authorization_id)
    JOIN pr06a_control.activation c USING(authorization_id)
    WHERE a.attempt=p_attempt AND pr06a_control.owns_batch(b,f) AND c.executor_login=session_user;
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_SENTENCE_BLOCKED'; END IF;
END $f$;

CREATE FUNCTION pr06a_api.complete(f uuid, p_attempt uuid, p_state text, p_stop text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  SELECT b0.* INTO STRICT b FROM pr06a_control.batch b0 JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b0,f) AND c.executor_login=session_user FOR UPDATE OF b0;
  IF p_attempt IS NOT NULL THEN
    UPDATE pr06a_control.attempts SET state=p_state
      WHERE attempt=p_attempt AND authorization_id=b.authorization_id AND state='submitting'
        AND (p_state IN ('failed','unknown') OR (p_state='succeeded'
          AND EXISTS(SELECT FROM pr06a_control.protocol WHERE attempt=p_attempt AND event='task-finished')
          AND EXISTS(SELECT FROM pr06a_control.recognition WHERE attempt=p_attempt)
          AND NOT EXISTS(
            SELECT FROM (SELECT DISTINCT ON (sentence_id) sentence_id,final,required_sample
              FROM pr06a_control.recognition WHERE attempt=p_attempt ORDER BY sentence_id,revision DESC) r
            JOIN pr06a_control.frames fr ON fr.attempt=p_attempt AND fr.end_sample=r.required_sample
            WHERE NOT r.final OR fr.disposition<>'confirmed')
          AND (p_stop IS NOT NULL OR (reserved_samples=allowed_samples AND usage_final
          AND NOT EXISTS(SELECT FROM pr06a_control.frames WHERE attempt=p_attempt AND disposition<>'confirmed')))));
    IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_COMPLETION_BLOCKED'; END IF;
  END IF;
  IF p_stop IS NOT NULL THEN
    UPDATE pr06a_control.batch SET closed=true,stop_code=coalesce(stop_code,p_stop)
      WHERE authorization_id=b.authorization_id;
  END IF;
END $f$;

CREATE FUNCTION pr06a_api.interrupt(f uuid, p_attempt uuid, p_stop text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  SELECT b0.* INTO b FROM pr06a_control.batch b0 JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b0,f) AND c.executor_login=session_user FOR UPDATE OF b0;
  IF NOT FOUND THEN RETURN; END IF;
  UPDATE pr06a_control.attempts SET state='unknown'
    WHERE authorization_id=b.authorization_id AND attempt=p_attempt AND state='submitting';
  UPDATE pr06a_control.batch SET closed=true,stop_code=coalesce(stop_code,p_stop)
    WHERE authorization_id=b.authorization_id;
END $f$;

CREATE FUNCTION pr06a_api.settle(p_attempt uuid,p_revision integer,p_request text,p_in bigint,p_out bigint,
    p_gross bigint,p_net bigint,p_month text,p_evidence text,p_metering_revision integer,p_resolved boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE; a pr06a_control.attempts%ROWTYPE;
  prior pr06a_control.bill_evidence%ROWTYPE; total numeric; matches_usage boolean;
BEGIN
  SELECT b0.* INTO STRICT b FROM pr06a_control.batch b0 JOIN pr06a_control.attempts a0 USING(authorization_id)
    WHERE a0.attempt=p_attempt FOR UPDATE OF b0;
  SELECT * INTO STRICT a FROM pr06a_control.attempts WHERE attempt=p_attempt FOR UPDATE;
  PERFORM billing_api.lock_budget(b.period_start);
  SELECT * INTO prior FROM pr06a_control.bill_evidence WHERE attempt=p_attempt AND revision=p_revision;
  IF FOUND THEN
    IF ROW(prior.request_id,prior.input_tokens,prior.output_tokens,prior.gross_microyuan,
      prior.net_microyuan,prior.bill_month,prior.evidence_hash,
      prior.reviewed_metering_revision,prior.discrepancy_resolved) IS DISTINCT FROM
      ROW(p_request,p_in,p_out,p_gross,p_net,p_month,p_evidence,p_metering_revision,p_resolved) THEN
      RAISE EXCEPTION 'PR06A_BILL_REVISION_CONFLICT';
    END IF;
    RETURN;
  END IF;
  IF (NOT b.closed AND clock_timestamp()<b.lease_until)
     OR p_revision IS DISTINCT FROM 1+(SELECT coalesce(max(revision),0) FROM pr06a_control.bill_evidence WHERE attempt=p_attempt)
     OR p_metering_revision IS NULL OR p_metering_revision>a.metering_revision
     OR (a.request_id IS NOT NULL AND p_request IS DISTINCT FROM a.request_id) THEN
    RAISE EXCEPTION 'PR06A_BILL_ATTRIBUTION_CONFLICT';
  END IF;
  INSERT INTO pr06a_control.bill_evidence(attempt,revision,request_id,input_tokens,output_tokens,
      gross_microyuan,net_microyuan,bill_month,evidence_hash,reviewed_metering_revision,discrepancy_resolved)
    VALUES(p_attempt,p_revision,p_request,p_in,p_out,p_gross,p_net,p_month,p_evidence,p_metering_revision,p_resolved);
  matches_usage:= p_metering_revision=a.metering_revision AND (p_resolved OR a.metering_revision=0 OR
    (p_in=a.input_tokens AND p_out=a.output_tokens AND p_in>=coalesce(a.observed_input,0)
     AND p_out>=coalesce(a.observed_output,0) AND p_in+p_out>=coalesce(a.observed_total,0)));
  UPDATE pr06a_control.attempts SET request_id=coalesce(p_request,a.request_id),gross_microyuan=p_gross,
    reconciled_metering_revision=CASE WHEN matches_usage THEN p_metering_revision END WHERE attempt=p_attempt;
  SELECT coalesce(sum(gross_microyuan),0) INTO total FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id;
  UPDATE pr06a_control.batch SET confirmed_microyuan=total,
    held_microyuan=CASE WHEN b.released THEN 0 ELSE greatest(0,b.authorized_microyuan-total)::bigint END,
    closed=b.closed OR total>=b.authorized_microyuan,
    stop_code=CASE WHEN total>=b.authorized_microyuan THEN coalesce(b.stop_code,'bill_limit') ELSE b.stop_code END
    WHERE authorization_id=b.authorization_id;
  PERFORM pr06a_control.alert_budget(b.period_start);
END $f$;

CREATE FUNCTION pr06a_api.release_reconciled() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  SELECT * INTO STRICT b FROM pr06a_control.batch FOR UPDATE;
  PERFORM billing_api.lock_budget(b.period_start);
  IF (NOT b.closed AND clock_timestamp()<b.lease_until)
      OR EXISTS(SELECT FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id
        AND (gross_microyuan IS NULL OR reconciled_metering_revision IS DISTINCT FROM metering_revision)) THEN
    RAISE EXCEPTION 'PR06A_EXPOSURE_UNRESOLVED';
  END IF;
  UPDATE pr06a_control.batch SET held_microyuan=0,released=true,
    closed=true,stop_code=coalesce(stop_code,'reconciled_closed') WHERE authorization_id=b.authorization_id;
END $f$;

CREATE TABLE pr06a_control.inbound (
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  sequence integer NOT NULL CHECK (sequence BETWEEN 1 AND 512),
  control_seen integer NOT NULL CHECK (control_seen BETWEEN 0 AND 2),
  frame_seen integer NOT NULL CHECK (frame_seen BETWEEN 0 AND 100),
  payload text NOT NULL CHECK (octet_length(payload)<=1048576),
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (attempt,sequence)
);

CREATE FUNCTION pr06a_api.record_inbound(f uuid, p_attempt uuid, p_sequence integer, p_control integer,
    p_frame integer, p_payload text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  INSERT INTO pr06a_control.inbound(attempt,sequence,control_seen,frame_seen,payload)
    SELECT a.attempt,p_sequence,p_control,p_frame,p_payload FROM pr06a_control.attempts a
    JOIN pr06a_control.batch b USING(authorization_id) JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b,f) AND c.executor_login=session_user AND a.attempt=p_attempt;
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_INBOUND_BLOCKED'; END IF;
END $f$;

CREATE TABLE pr06a_control.anomalies (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  code text NOT NULL,
  observed_hash text NOT NULL CHECK (observed_hash ~ '^[a-f0-9]{64}$'),
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION pr06a_api.anomaly(f uuid, p_attempt uuid, p_code text,p_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  INSERT INTO pr06a_control.anomalies(attempt,code,observed_hash)
    SELECT a.attempt,p_code,p_hash FROM pr06a_control.attempts a JOIN pr06a_control.batch b USING(authorization_id)
    JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b,f) AND c.executor_login=session_user AND a.attempt=p_attempt;
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_ANOMALY_BLOCKED'; END IF;
END $f$;

CREATE TABLE pr06a_control.protocol (
  attempt uuid NOT NULL REFERENCES pr06a_control.attempts(attempt),
  event text NOT NULL CHECK (event IN ('handshake-intent','handshake','run-task','task-started','finish-task','task-finished','task-failed')),
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (attempt,event)
);

CREATE FUNCTION pr06a_api.protocol(f uuid, p_attempt uuid, p_event text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
BEGIN
  INSERT INTO pr06a_control.protocol(attempt,event)
    SELECT a.attempt,p_event FROM pr06a_control.attempts a JOIN pr06a_control.batch b USING(authorization_id)
    JOIN pr06a_control.activation c USING(authorization_id)
    WHERE pr06a_control.owns_batch(b,f) AND c.executor_login=session_user AND a.attempt=p_attempt;
  IF NOT FOUND THEN RAISE EXCEPTION 'PR06A_PROTOCOL_BLOCKED'; END IF;
END $f$;

CREATE FUNCTION pr06a_api.check_connection(f uuid, p_attempt uuid, p_handshake boolean) RETURNS double precision
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $f$
DECLARE b pr06a_control.batch%ROWTYPE;
BEGIN
  b:=pr06a_control.check_batch(f);
  IF NOT EXISTS (SELECT FROM pr06a_control.attempts WHERE authorization_id=b.authorization_id
      AND attempt=p_attempt AND state='submitting' AND clock_timestamp()<lease_until) THEN
    RAISE EXCEPTION 'PR06A_CONNECTION_BLOCKED';
  END IF;
  IF p_handshake THEN
    INSERT INTO pr06a_control.protocol(attempt,event) VALUES(p_attempt,'handshake-intent');
  END IF;
  RETURN pr06a_control.dispatch_seconds(b,p_attempt);
END $f$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pr06a_api,pr06a_control FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pr06a_control.risk_microyuan(bigint,bigint,bigint) TO classroom_billing_control;
GRANT EXECUTE ON FUNCTION pr06a_api.open_batch(uuid,jsonb,text,jsonb),
  pr06a_api.claim_attempt(uuid,integer,uuid,uuid,uuid), pr06a_api.frame_intent(uuid,uuid,integer,integer,text),
  pr06a_api.confirm_frame(uuid,uuid,integer), pr06a_api.observe(uuid,uuid,text,bigint,bigint,boolean),
  pr06a_api.record_sentence(uuid,uuid,bigint,integer,bigint,bigint,boolean,text,integer), pr06a_api.complete(uuid,uuid,text,text),
  pr06a_api.protocol(uuid,uuid,text),pr06a_api.check_connection(uuid,uuid,boolean),
  pr06a_api.discard_frame(uuid,uuid,integer),pr06a_api.anomaly(uuid,uuid,text,text),
  pr06a_api.record_inbound(uuid,uuid,integer,integer,integer,text),
  pr06a_api.check_frame(uuid,uuid,integer),pr06a_api.observe_risk(uuid,uuid,bigint,bigint,bigint),
  pr06a_api.lock_dispatch(uuid,boolean),
  pr06a_api.interrupt(uuid,uuid,text)
  TO classroom_pr06a_executor;
GRANT EXECUTE ON FUNCTION pr06a_api.settle(uuid,integer,text,bigint,bigint,bigint,bigint,text,text,integer,boolean),
  pr06a_api.release_reconciled() TO classroom_pr06a_reconciler;
