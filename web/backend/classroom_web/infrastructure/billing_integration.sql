-- PR-11a integration candidate, applied after billing_schema.sql.
-- Coordinator assigns the formal revision and 04a predecessor.
GRANT USAGE ON SCHEMA billing_api TO classroom_web_session_writer;
ALTER TABLE public.quota_reservations ADD CONSTRAINT billing_reservation_period
  FOREIGN KEY(period_start) REFERENCES billing_control.platform_budgets(period_start);
GRANT SELECT ON public.price_catalog TO classroom_billing_control;
GRANT SELECT(id,max_active_users,registration_enabled,revision,activated_count) ON public.identity_capacity TO classroom_billing_control;
GRANT UPDATE(registration_enabled,revision,updated_at) ON public.identity_capacity TO classroom_billing_control;
CREATE POLICY billing_capacity ON public.identity_capacity TO classroom_billing_control USING(true) WITH CHECK(true);
GRANT EXECUTE ON FUNCTION public.classroom_web_identity_usable(uuid) TO classroom_billing_control;
GRANT SELECT(id,owner_id,state,may_have_charged,lease_expires_at,class_session_id,cancel_requested_at)
  ON public.tasks TO classroom_billing_control;
GRANT SELECT(id,owner_id,task_id,submitted_at,state) ON public.task_attempts TO classroom_billing_control;
CREATE POLICY billing_execution_metadata ON public.tasks FOR SELECT TO classroom_billing_control
  USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);
CREATE POLICY billing_attempt_metadata ON public.task_attempts FOR SELECT TO classroom_billing_control
  USING(owner_id=nullif(current_setting('app.owner_id',true),'')::uuid);

ALTER TABLE billing_control.exposures ADD COLUMN owner_id uuid;
ALTER TABLE billing_control.exposures ADD COLUMN task_id uuid;
ALTER TABLE billing_control.exposures ADD COLUMN state text;
ALTER TABLE billing_control.exposures ADD COLUMN price_revision text;
ALTER TABLE billing_control.exposures ADD COLUMN reconciled_at timestamptz;
ALTER TABLE billing_control.exposures ADD COLUMN checked_at timestamptz;
-- Trusted migrator backfills metadata only, including old synthetic ledgers.
UPDATE billing_control.exposures e SET owner_id=r.owner_id,task_id=r.task_id,state=r.state,price_revision=p.revision
  FROM public.quota_reservations r JOIN public.price_catalog p ON p.id=r.price_id WHERE e.reservation_id=r.id;
CREATE FUNCTION billing_api.exposure_metadata() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
BEGIN
  SELECT r.owner_id,r.task_id,r.state,p.revision INTO NEW.owner_id,NEW.task_id,NEW.state,NEW.price_revision
    FROM public.quota_reservations r JOIN public.price_catalog p ON p.id=r.price_id WHERE r.id=NEW.reservation_id;
  SELECT max(created_at) INTO NEW.reconciled_at FROM public.usage_receipts WHERE reservation_id=NEW.reservation_id;
  RETURN NEW;
END $f$;
ALTER FUNCTION billing_api.exposure_metadata() OWNER TO classroom_billing_control;
CREATE TRIGGER exposure_metadata BEFORE INSERT OR UPDATE OF held,unknown,settled ON billing_control.exposures
  FOR EACH ROW EXECUTE FUNCTION billing_api.exposure_metadata();

CREATE TABLE billing_control.notification_outbox(
  id uuid PRIMARY KEY,period_start timestamptz NOT NULL,threshold integer NOT NULL,
  delivered_at timestamptz,UNIQUE(period_start,threshold)
);
GRANT SELECT,INSERT,UPDATE ON billing_control.notification_outbox TO classroom_billing_control;
CREATE FUNCTION billing_api.queue_threshold() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
BEGIN
  INSERT INTO billing_control.notification_outbox VALUES(gen_random_uuid(),NEW.period_start,NEW.threshold,NULL)
    ON CONFLICT(period_start,threshold) DO NOTHING;
  RETURN NEW;
END $f$;
ALTER FUNCTION billing_api.queue_threshold() OWNER TO classroom_billing_control;
CREATE TRIGGER queue_threshold AFTER INSERT ON billing_control.alerts
  FOR EACH ROW EXECUTE FUNCTION billing_api.queue_threshold();
INSERT INTO billing_control.notification_outbox
  SELECT gen_random_uuid(),period_start,threshold,NULL FROM billing_control.alerts ON CONFLICT DO NOTHING;

CREATE FUNCTION billing_api.execution_metadata(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE t record;a record;active boolean;
BEGIN
  SELECT id,state,may_have_charged,lease_expires_at,cancel_requested_at INTO t FROM public.tasks WHERE id=p_task;
  IF NOT FOUND THEN RETURN NULL;END IF;
  SELECT id,submitted_at,state INTO a FROM public.task_attempts WHERE task_id=p_task ORDER BY submitted_at DESC NULLS LAST,id LIMIT 1;
  active:=public.classroom_web_identity_usable(nullif(current_setting('app.owner_id',true),'')::uuid);
  RETURN jsonb_build_object('state',t.state,'may_have_charged',t.may_have_charged,
    'lease_expired',t.lease_expires_at IS NOT NULL AND t.lease_expires_at<=clock_timestamp(),
    'cancel_requested',t.cancel_requested_at IS NOT NULL,'owner_active',coalesce(active,false),
    'attempt_id',a.id,'submitted_at',a.submitted_at,'attempt_state',a.state);
END $f$;
ALTER FUNCTION billing_api.execution_metadata(uuid) OWNER TO classroom_billing_control;

-- Derive the two groups from the same snapshot for current reads and stored receipts.
CREATE FUNCTION billing_api.with_switch_summaries(snapshot jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $f$
  SELECT snapshot || jsonb_build_object(
    'asr_enabled',(snapshot->>'asr_streaming_enabled')::boolean OR (snapshot->>'asr_filetrans_enabled')::boolean,
    'ai_enabled',(snapshot->>'ai_interaction_enabled')::boolean OR (snapshot->>'ai_summary_enabled')::boolean)
$f$;
ALTER FUNCTION billing_api.with_switch_summaries(jsonb) OWNER TO classroom_billing_control;

CREATE FUNCTION billing_api.switch_view() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE s jsonb;c record;
BEGIN
  s:=billing_api.lock_switches();SELECT max_active_users,registration_enabled INTO STRICT c FROM public.identity_capacity WHERE id=1;
  RETURN billing_api.with_switch_summaries(jsonb_build_object('revision',s->'revision','registration_enabled',c.registration_enabled,
    'max_active_users',c.max_active_users::text,
    'billing_enabled',s->'flags'->'billing','new_capture_enabled',s->'flags'->'new_capture',
    'asr_streaming_enabled',s->'flags'->'asr_streaming','asr_filetrans_enabled',s->'flags'->'asr_filetrans',
    'ai_interaction_enabled',s->'flags'->'ai_interaction','ai_summary_enabled',s->'flags'->'ai_summary'));
END $f$;
ALTER FUNCTION billing_api.switch_view() OWNER TO classroom_billing_control;

CREATE FUNCTION billing_api.apply_switch_body(actor uuid,op uuid,body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE flags jsonb:='{}';k text;c record;capture_flag jsonb;previous boolean;
BEGIN
  SELECT switches.flags->'new_capture' INTO STRICT capture_flag FROM billing_control.switches WHERE singleton FOR UPDATE;
  IF body ? 'new_capture_enabled' AND body->'new_capture_enabled' IS DISTINCT FROM capture_flag THEN
    RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
  SELECT max_active_users INTO STRICT c FROM public.identity_capacity WHERE id=1;
  IF body ? 'max_active_users' AND (body->>'max_active_users')::integer<>c.max_active_users THEN
    RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
  FOREACH k IN ARRAY ARRAY['billing','new_capture','asr_streaming','asr_filetrans','ai_interaction','ai_summary'] LOOP
    IF body ? (k||'_enabled') THEN flags:=flags||jsonb_build_object(k,body->(k||'_enabled'));END IF;
  END LOOP;
  IF body ? 'asr_enabled' THEN flags:=flags||jsonb_build_object('asr_streaming',body->'asr_enabled','asr_filetrans',body->'asr_enabled');END IF;
  IF body ? 'ai_enabled' THEN flags:=flags||jsonb_build_object('ai_interaction',body->'ai_enabled','ai_summary',body->'ai_enabled');END IF;
  IF body ? 'registration_enabled' THEN flags:=flags||jsonb_build_object('registration',body->'registration_enabled');END IF;
  -- apply_admin replays the committed HTTP snapshot. An audit alone cannot reconstruct it.
  SELECT EXISTS(SELECT FROM billing_control.audit WHERE action='switches' AND actor_id=actor AND op_id=op) INTO previous;
  PERFORM billing_api.update_switches(actor,op,(body->>'base_revision')::bigint,flags,body->>'reason');
  IF previous THEN RETURN jsonb_build_object('error','RESULT_UNKNOWN');END IF;
  IF body ? 'registration_enabled' THEN
    UPDATE public.identity_capacity SET registration_enabled=(body->>'registration_enabled')::boolean,
      revision=revision+1,updated_at=clock_timestamp() WHERE id=1;
  END IF;
  RETURN billing_api.switch_view();
END $f$;
ALTER FUNCTION billing_api.apply_switch_body(uuid,uuid,jsonb) OWNER TO classroom_billing_control;

CREATE FUNCTION billing_api.usage_view(p_start timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE b jsonb;v text;at_time timestamptz;
BEGIN
  b:=billing_api.lock_budget(p_start);
  SELECT CASE WHEN count(DISTINCT price_revision)=1 THEN min(price_revision) ELSE 'multiple-or-unreconciled' END,
    max(reconciled_at) INTO v,at_time FROM billing_control.exposures WHERE period_start=p_start;
  RETURN jsonb_build_object('period_start',b->'period_start','period_end',b->'period_end',
    'budget_microyuan',b->>'budget_microyuan','fixed_and_accrued_microyuan',b->>'fixed_microyuan',
    'settled_microyuan',b->>'settled','reserved_microyuan',b->>'held','unknown_microyuan',b->>'unknown',
    'safety_reserve_microyuan',b->>'safety_microyuan','price_version',v,'ledger_reconciled_at',at_time);
END $f$;
ALTER FUNCTION billing_api.usage_view(timestamptz) OWNER TO classroom_billing_control;

-- Existing identity owner is the only bridge allowed to inspect or consume
-- password/MFA attestation and bound reauth rows. Gateway logins see JSON only.
GRANT EXECUTE ON FUNCTION billing_api.with_switch_summaries(jsonb),billing_api.switch_view(),billing_api.apply_switch_body(uuid,uuid,jsonb),
  billing_api.adjust_quota(uuid,uuid,uuid,bigint,text),billing_api.usage_view(timestamptz) TO classroom_web_session_writer;
GRANT INSERT ON public.quota_periods TO classroom_billing_control;
CREATE FUNCTION billing_api.adjustment_period(b jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE q uuid;at_time timestamptz:=clock_timestamp();start_at timestamptz;end_at timestamptz;
  requested timestamptz:=(b->>'period_start')::timestamptz;resource_name text:=b->>'bucket_kind';budget jsonb;lim bigint;
BEGIN
  IF NOT public.classroom_web_identity_usable((b->>'user_id')::uuid) THEN RETURN NULL;END IF;
  budget:=billing_api.lock_budget(date_trunc('month',requested AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai');
  SELECT id INTO q FROM public.quota_periods WHERE owner_id=(b->>'user_id')::uuid AND resource=resource_name AND period_start=requested;
  IF FOUND THEN RETURN q;END IF;
  start_at:=date_trunc(CASE WHEN resource_name='asr_day_ms' THEN 'day' ELSE 'month' END,at_time AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai';
  IF requested<>start_at THEN RETURN NULL;END IF;
  end_at:=((start_at AT TIME ZONE 'Asia/Shanghai')+CASE WHEN resource_name='asr_day_ms' THEN interval '1 day' ELSE interval '1 month' END) AT TIME ZONE 'Asia/Shanghai';
  lim:=CASE resource_name WHEN 'asr_day_ms' THEN 10800000
    WHEN 'asr_month_ms' THEN CASE WHEN (budget->>'stage')::integer=10 THEN 43200000 ELSE 64800000 END
    WHEN 'retranscribe_month_ms' THEN CASE WHEN (budget->>'stage')::integer=10 THEN 8640000 ELSE 12960000 END
    WHEN 'interactive_month_count' THEN CASE WHEN (budget->>'stage')::integer=10 THEN 20 ELSE 36 END
    WHEN 'summary_month_count' THEN CASE WHEN (budget->>'stage')::integer=10 THEN 8 ELSE 12 END END;
  IF lim IS NULL THEN RETURN NULL;END IF;
  INSERT INTO public.quota_periods(id,owner_id,resource,period_start,period_end,limit_value)
    VALUES(gen_random_uuid(),(b->>'user_id')::uuid,resource_name,start_at,end_at,lim)
    ON CONFLICT(owner_id,resource,period_start) DO NOTHING;
  SELECT id INTO q FROM public.quota_periods WHERE owner_id=(b->>'user_id')::uuid AND resource=resource_name AND period_start=requested;
  RETURN q;
END $f$;
ALTER FUNCTION billing_api.adjustment_period(jsonb) OWNER TO classroom_billing_control;
GRANT EXECUTE ON FUNCTION billing_api.adjustment_period(jsonb) TO classroom_web_session_writer;
CREATE FUNCTION billing_api.issue_admin_proof(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE s jsonb;a public.auth_challenges%ROWTYPE;n timestamptz;
BEGIN
  s:=identity_api.lock_account(p);
  IF s IS NULL OR s->>'role'<>'admin' OR s->>'mfa_level'<>'totp' THEN RETURN jsonb_build_object('error','FORBIDDEN');END IF;
  SELECT * INTO a FROM public.auth_challenges WHERE token_hash=p->>'proof_hash' AND kind='verified'
    AND owner_id=(s->>'owner_id')::uuid FOR UPDATE;
  n:=clock_timestamp();
  IF a.id IS NULL OR a.consumed_at IS NOT NULL OR n>=a.expires_at OR a.security_epoch<>(s->>'epoch')::bigint
    OR a.scope->>'mode'<>'reauth' OR a.scope->>'mfa_level'<>'totp'
    OR a.scope->>'binding' IS DISTINCT FROM p->>'commitment'
    OR p->>'purpose' NOT IN ('adminUpdateSwitches','adminAdjustQuota') OR p->>'target_id'<>s->>'owner_id' THEN RETURN NULL;END IF;
  INSERT INTO public.reauth_proofs(id,owner_id,session_id,device_id,security_epoch,token_hash,purpose,target_id,
      commitment,operation_key,verified_at,expires_at)
    VALUES((p->>'id')::uuid,a.owner_id,(s->>'session_id')::uuid,(s->>'device_id')::uuid,a.security_epoch,p->>'reauth_hash',
      p->>'purpose',a.owner_id,p->>'commitment',(p->>'operation_key')::uuid,(a.scope->>'verified_at')::timestamptz,
      least(a.expires_at,(s->>'expires_at')::timestamptz));
  UPDATE public.auth_challenges SET consumed_at=n WHERE id=a.id;
  RETURN jsonb_build_object('expires_at',least(a.expires_at,(s->>'expires_at')::timestamptz));
END $f$;
ALTER FUNCTION billing_api.issue_admin_proof(jsonb) OWNER TO classroom_web_session_writer;

CREATE FUNCTION billing_api.apply_admin(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE s jsonb;r public.reauth_proofs%ROWTYPE;old public.preauth_receipts%ROWTYPE;
  answer jsonb;n timestamptz;uid uuid;action text:=p->>'purpose';b jsonb:=p->'body';q uuid;
  receipt_context text;op uuid:=(p->>'operation_key')::uuid;
BEGIN
  s:=identity_api.lock_account(p);
  IF s IS NULL OR s->>'role'<>'admin' OR s->>'mfa_level'<>'totp' THEN RETURN jsonb_build_object('error','FORBIDDEN');END IF;
  uid:=(s->>'owner_id')::uuid;
  IF action NOT IN ('adminUpdateSwitches','adminAdjustQuota') OR b->>'reason' IS NULL OR length(btrim(b->>'reason'))=0 THEN
    RETURN jsonb_build_object('error','VALIDATION_FAILED');END IF;
  receipt_context:=CASE WHEN action='adminUpdateSwitches' THEN uid::text ELSE p->>'token_hash' END;
  SELECT * INTO old FROM public.preauth_receipts WHERE context_hash=receipt_context AND operation=action
    AND operation_key=op;
  IF NOT FOUND AND action='adminUpdateSwitches' THEN
    -- Earlier candidates keyed snapshots by session. A later login could have saved
    -- another revision for the same write, so use the first committed response.
    SELECT pr.* INTO old FROM public.auth_sessions sessions JOIN public.preauth_receipts pr
      ON pr.context_hash=sessions.token_hash WHERE sessions.owner_id=uid AND pr.operation=action AND pr.operation_key=op
      ORDER BY pr.created_at,pr.context_hash LIMIT 1;
  END IF;
  IF old.operation_key IS NOT NULL THEN
    IF old.commitment IS DISTINCT FROM p->>'commitment' THEN RETURN jsonb_build_object('error','IDEMPOTENCY_MISMATCH');END IF;
    IF action='adminAdjustQuota' THEN RETURN old.result;END IF;
  END IF;
  SELECT * INTO r FROM public.reauth_proofs WHERE token_hash=p->>'reauth_hash' AND owner_id=uid FOR UPDATE;
  n:=clock_timestamp();
  IF r.id IS NULL OR r.session_id<>(s->>'session_id')::uuid OR r.device_id<>(s->>'device_id')::uuid OR r.security_epoch<>(s->>'epoch')::bigint
    OR r.purpose<>action OR r.target_id<>uid OR r.commitment IS DISTINCT FROM p->>'commitment'
    OR r.operation_key<>op OR (r.consumed_at IS NULL AND n>=r.expires_at)
    OR (r.consumed_at IS NOT NULL AND old.operation_key IS NULL) THEN
    RETURN jsonb_build_object('error','REAUTH_REQUIRED');END IF;
  IF old.operation_key IS NOT NULL THEN
    IF r.consumed_at IS NULL THEN UPDATE public.reauth_proofs SET consumed_at=n WHERE id=r.id;END IF;
    RETURN billing_api.with_switch_summaries(old.result);
  ELSIF action='adminUpdateSwitches' THEN
    answer:=billing_api.apply_switch_body(uid,op,b);
    IF answer ? 'error' THEN RETURN answer;END IF;
  ELSE
    PERFORM set_config('app.owner_id',b->>'user_id',true);
    q:=billing_api.adjustment_period(b);
    IF q IS NULL THEN RETURN jsonb_build_object('error','NOT_FOUND');END IF;
    PERFORM billing_api.adjust_quota(uid,op,q,(b->>'additional_units')::bigint,b->>'reason');
    answer:=jsonb_build_object('user_id',b->>'user_id');
  END IF;
  IF clock_timestamp()>=r.expires_at THEN RAISE EXCEPTION 'reauth expired' USING ERRCODE='42501';END IF;
  UPDATE public.reauth_proofs SET consumed_at=clock_timestamp() WHERE id=r.id;
  INSERT INTO public.preauth_receipts(context_hash,operation,operation_key,commitment,secret,result)
    VALUES(receipt_context,action,op,p->>'commitment',false,answer);
  RETURN answer;
END $f$;
ALTER FUNCTION billing_api.apply_admin(jsonb) OWNER TO classroom_web_session_writer;

CREATE FUNCTION billing_api.read_admin(p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE s jsonb;
BEGIN
  s:=identity_api.validate_session(p);
  IF s IS NULL OR s->>'role'<>'admin' OR s->>'mfa_level'<>'totp' THEN RETURN jsonb_build_object('error','FORBIDDEN');END IF;
  IF p->>'kind'='switches' THEN RETURN billing_api.switch_view();END IF;
  RETURN billing_api.usage_view(coalesce((p->>'period_start')::timestamptz,
    date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'));
END $f$;
ALTER FUNCTION billing_api.read_admin(jsonb) OWNER TO classroom_web_session_writer;

CREATE FUNCTION billing_api.maintain_period(actor uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE start_at timestamptz;stage integer;b record;
BEGIN
  start_at:=date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai';
  PERFORM pg_advisory_xact_lock(hashtextextended('billing-period:'||start_at,0));
  SELECT * INTO b FROM billing_control.platform_budgets WHERE period_start=start_at;
  IF FOUND THEN RETURN jsonb_build_object('period_start',start_at,'stage',b.stage,'opened',false);END IF;
  SELECT max_active_users INTO STRICT stage FROM public.identity_capacity WHERE id=1;
  PERFORM billing_api.open_period(start_at,stage,actor,md5('billing-period:'||start_at)::uuid,'Scheduled calendar cycle');
  RETURN jsonb_build_object('period_start',start_at,'stage',stage,'opened',true);
END $f$;
ALTER FUNCTION billing_api.maintain_period(uuid) OWNER TO classroom_billing_control;
CREATE FUNCTION billing_api.pending_fee_tasks() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $f$
  WITH picked AS (SELECT reservation_id FROM billing_control.exposures WHERE state IN ('reserved','submitted','unknown')
      ORDER BY checked_at NULLS FIRST,reservation_id LIMIT 100 FOR UPDATE SKIP LOCKED),
    updated AS (UPDATE billing_control.exposures e SET checked_at=clock_timestamp() FROM picked p
      WHERE e.reservation_id=p.reservation_id RETURNING e.owner_id,e.task_id)
  SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM updated t
$f$;
ALTER FUNCTION billing_api.pending_fee_tasks() OWNER TO classroom_billing_control;
CREATE FUNCTION billing_api.threshold_messages(p_ack uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE answer jsonb;
BEGIN
  IF p_ack IS NOT NULL THEN UPDATE billing_control.notification_outbox SET delivered_at=coalesce(delivered_at,clock_timestamp()) WHERE id=p_ack;END IF;
  SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') INTO answer FROM
    (SELECT id,period_start,threshold FROM billing_control.notification_outbox WHERE delivered_at IS NULL ORDER BY period_start,threshold LIMIT 100) t;
  RETURN answer;
END $f$;
ALTER FUNCTION billing_api.threshold_messages(uuid) OWNER TO classroom_billing_control;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA billing_api FROM PUBLIC;
GRANT EXECUTE ON FUNCTION billing_api.execution_metadata(uuid),billing_api.pending_fee_tasks() TO classroom_billing_reconciler;
GRANT EXECUTE ON FUNCTION billing_api.maintain_period(uuid),billing_api.threshold_messages(uuid) TO classroom_billing_operator;

-- The approval lives on the optional Filetrans migration. Legacy synthetic
-- billing databases can still install and use the independent billing seam.
DO $filetrans$
BEGIN
    IF to_regprocedure('public.classroom_web_approve_filetrans(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION public.classroom_web_approve_filetrans(uuid,uuid,uuid,uuid,uuid,uuid,text,timestamptz)
            TO classroom_billing_operator;
    END IF;
END
$filetrans$;
