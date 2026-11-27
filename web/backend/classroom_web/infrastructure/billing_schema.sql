-- PR-11a candidate DDL. The coordinator assigns the Alembic revision and parent.
-- Install transactionally after 0008 in a dedicated synthetic database.
DO $roles$
DECLARE n text;
BEGIN
  FOREACH n IN ARRAY ARRAY['classroom_billing_control','classroom_billing_operator','classroom_billing_reconciler'] LOOP
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=n) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',n);
    ELSIF EXISTS(SELECT FROM pg_roles WHERE rolname=n AND
        (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication)) THEN
      RAISE EXCEPTION 'unsafe billing role';
    END IF;
    IF EXISTS(SELECT FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=n)) THEN
      RAISE EXCEPTION 'billing group must not inherit roles';
    END IF;
  END LOOP;
END $roles$;
CREATE SCHEMA billing_control;
CREATE SCHEMA billing_api;
REVOKE ALL ON SCHEMA billing_control,billing_api FROM PUBLIC;
GRANT USAGE ON SCHEMA billing_control,billing_api,public TO classroom_billing_control;
GRANT USAGE ON SCHEMA billing_api TO classroom_web_runtime,classroom_billing_operator,classroom_billing_reconciler;
GRANT USAGE ON SCHEMA public TO classroom_billing_reconciler;

CREATE TABLE public.price_catalog(
  id uuid PRIMARY KEY, provider text NOT NULL, model text NOT NULL, region text NOT NULL,
  revision text NOT NULL, currency text NOT NULL CHECK(currency='CNY'),
  rates jsonb NOT NULL CHECK(jsonb_typeof(rates)='object'),
  minimum_microyuan bigint NOT NULL CHECK(minimum_microyuan>=0),
  synthetic boolean NOT NULL, effective_at timestamptz NOT NULL, verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(provider,model,region,revision)
);
CREATE TABLE public.quota_periods(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),
  resource text NOT NULL CHECK(resource IN ('asr_month_ms','asr_day_ms','retranscribe_month_ms','interactive_month_count','summary_month_count')),
  period_start timestamptz NOT NULL,period_end timestamptz NOT NULL,
  limit_value bigint NOT NULL CONSTRAINT quota_limit_safe_integer CHECK(limit_value BETWEEN 0 AND 9007199254740991),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(owner_id,id),UNIQUE(owner_id,resource,period_start),CHECK(period_end>period_start)
);
ALTER TABLE public.task_attempts ADD CONSTRAINT billing_attempt_exact_task UNIQUE(owner_id,id,task_id);
CREATE TABLE public.quota_reservations(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES public.users(id),
  task_id uuid NOT NULL,attempt_id uuid,quota_root_id uuid,
  period_start timestamptz NOT NULL,purpose text NOT NULL,
  state text NOT NULL CHECK(state IN ('reserved','submitted','unknown','settled','released')),
  pool text NOT NULL CHECK(pool IN ('asr','filetrans','ai')),
  reserved_microyuan bigint NOT NULL CHECK(reserved_microyuan>=0),
  settled_microyuan bigint CHECK(settled_microyuan>=0),
  maximum_units jsonb NOT NULL,price_id uuid NOT NULL REFERENCES public.price_catalog(id),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(owner_id,id),UNIQUE(owner_id,task_id),UNIQUE(owner_id,attempt_id),
  FOREIGN KEY(owner_id,task_id) REFERENCES public.tasks(owner_id,id),
  FOREIGN KEY(owner_id,attempt_id,task_id) REFERENCES public.task_attempts(owner_id,id,task_id),
  FOREIGN KEY(owner_id,quota_root_id) REFERENCES public.quota_reservations(owner_id,id)
);
CREATE TABLE public.reservation_allocations(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL,reservation_id uuid NOT NULL,quota_period_id uuid NOT NULL,
  quantity bigint NOT NULL CHECK(quantity>=0),consumed_quantity bigint CHECK(consumed_quantity>=0),
  UNIQUE(owner_id,id),UNIQUE(owner_id,reservation_id,quota_period_id),
  FOREIGN KEY(owner_id,reservation_id) REFERENCES public.quota_reservations(owner_id,id),
  FOREIGN KEY(owner_id,quota_period_id) REFERENCES public.quota_periods(owner_id,id)
);
CREATE TABLE public.usage_receipts(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL,reservation_id uuid NOT NULL,attempt_id uuid NOT NULL,
  provider text NOT NULL,provider_request_id text NOT NULL CHECK(length(provider_request_id) BETWEEN 1 AND 200),
  meter text NOT NULL CHECK(length(meter) BETWEEN 1 AND 64),metric_revision bigint NOT NULL CHECK(metric_revision>=0),
  quantities jsonb,amount_microyuan bigint CHECK(amount_microyuan>=0),final boolean NOT NULL,
  bill_month text CHECK(bill_month~'^[0-9]{4}-(0[1-9]|1[0-2])$'),
  adjustment_of uuid REFERENCES public.usage_receipts(id),request_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(owner_id,id),UNIQUE(provider,provider_request_id,meter,metric_revision),
  FOREIGN KEY(owner_id,reservation_id) REFERENCES public.quota_reservations(owner_id,id),
  FOREIGN KEY(owner_id,attempt_id) REFERENCES public.task_attempts(owner_id,id)
);
CREATE TABLE public.billing_permits(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL,reservation_id uuid NOT NULL,submission_key uuid NOT NULL,
  quantities jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(owner_id,reservation_id,submission_key),
  FOREIGN KEY(owner_id,reservation_id) REFERENCES public.quota_reservations(owner_id,id)
);
CREATE INDEX billing_usage_reservation ON public.usage_receipts(owner_id,reservation_id,metric_revision);
CREATE TABLE public.quota_adjustments(
  id uuid PRIMARY KEY,owner_id uuid NOT NULL,quota_period_id uuid NOT NULL,
  delta bigint NOT NULL CHECK(delta>0),reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
  admin_actor_id uuid NOT NULL REFERENCES public.users(id),op_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(admin_actor_id,op_id),
  FOREIGN KEY(owner_id,quota_period_id) REFERENCES public.quota_periods(owner_id,id)
);
CREATE TABLE billing_control.platform_budgets(
  period_start timestamptz PRIMARY KEY,period_end timestamptz NOT NULL,stage integer NOT NULL CHECK(stage IN (10,30)),
  budget_microyuan bigint NOT NULL CHECK(budget_microyuan>0),
  safety_microyuan bigint NOT NULL CHECK(safety_microyuan>=0),fixed_microyuan bigint NOT NULL CHECK(fixed_microyuan>=0),
  pools jsonb NOT NULL,CHECK(period_end>period_start),CHECK(safety_microyuan<=budget_microyuan)
);
CREATE TABLE billing_control.exposures(
  reservation_id uuid PRIMARY KEY REFERENCES public.quota_reservations(id),
  period_start timestamptz NOT NULL REFERENCES billing_control.platform_budgets(period_start),
  pool text NOT NULL,held bigint NOT NULL CHECK(held>=0),unknown bigint NOT NULL CHECK(unknown>=0),
  settled bigint NOT NULL CHECK(settled>=0)
);
CREATE TABLE billing_control.switches(
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),revision bigint NOT NULL DEFAULT 1,
  flags jsonb NOT NULL
);
INSERT INTO billing_control.switches(flags) VALUES
  ('{"billing":false,"registration":false,"new_capture":false,"asr_streaming":false,"asr_filetrans":false,"ai_interaction":false,"ai_summary":false}');
CREATE TABLE billing_control.audit(
  id uuid PRIMARY KEY,op_id uuid NOT NULL,actor_id uuid NOT NULL,
  action text NOT NULL,reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
  detail jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX billing_switch_audit_op ON billing_control.audit(actor_id,op_id) WHERE action='switches';
CREATE UNIQUE INDEX billing_quota_audit_op ON billing_control.audit(actor_id,op_id) WHERE action='quota_adjustment';
CREATE UNIQUE INDEX billing_other_audit_op ON billing_control.audit(op_id) WHERE action NOT IN ('switches','quota_adjustment');
CREATE TABLE billing_control.alerts(
  period_start timestamptz NOT NULL REFERENCES billing_control.platform_budgets(period_start),
  threshold integer NOT NULL CHECK(threshold IN (60,80,100)),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(period_start,threshold)
);

DO $rls$
DECLARE n text;
BEGIN
  FOREACH n IN ARRAY ARRAY['quota_periods','quota_reservations','reservation_allocations','usage_receipts','quota_adjustments','billing_permits'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',n);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',n);
    EXECUTE format('CREATE POLICY billing_owner ON public.%I USING(owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid) WITH CHECK(owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid)',n);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC',n);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE ON public.%I TO classroom_web_runtime',n);
    EXECUTE format('GRANT SELECT ON public.%I TO classroom_billing_control,classroom_billing_reconciler',n);
  END LOOP;
END $rls$;
REVOKE INSERT,UPDATE ON public.quota_adjustments FROM classroom_web_runtime;
GRANT INSERT ON public.usage_receipts TO classroom_billing_reconciler;
GRANT UPDATE ON public.quota_reservations,public.reservation_allocations TO classroom_billing_reconciler;
GRANT SELECT ON public.price_catalog TO classroom_web_runtime,classroom_billing_reconciler;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA billing_control TO classroom_billing_control;
GRANT INSERT ON public.quota_adjustments TO classroom_billing_control;
GRANT UPDATE(limit_value) ON public.quota_periods TO classroom_billing_control;

-- History is append-only for all application roles, including reconcilers.
CREATE FUNCTION billing_api.immutable_history() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN RAISE EXCEPTION 'billing history is immutable' USING ERRCODE='23514'; END $f$;
CREATE TRIGGER price_immutable BEFORE UPDATE OR DELETE ON public.price_catalog
  FOR EACH ROW EXECUTE FUNCTION billing_api.immutable_history();
CREATE TRIGGER usage_immutable BEFORE UPDATE OR DELETE ON public.usage_receipts
  FOR EACH ROW EXECUTE FUNCTION billing_api.immutable_history();
CREATE TRIGGER adjustment_immutable BEFORE UPDATE OR DELETE ON public.quota_adjustments
  FOR EACH ROW EXECUTE FUNCTION billing_api.immutable_history();
CREATE TRIGGER permit_immutable BEFORE UPDATE OR DELETE ON public.billing_permits
  FOR EACH ROW EXECUTE FUNCTION billing_api.immutable_history();
CREATE TRIGGER billing_audit_immutable BEFORE UPDATE OR DELETE ON billing_control.audit
  FOR EACH ROW EXECUTE FUNCTION billing_api.immutable_history();

CREATE FUNCTION billing_api.lock_budget(p_start timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control AS $f$
DECLARE b billing_control.platform_budgets%ROWTYPE;totals jsonb;pool_totals jsonb;
BEGIN
  SELECT * INTO STRICT b FROM billing_control.platform_budgets WHERE period_start=p_start FOR UPDATE;
  SELECT jsonb_build_object('held',coalesce(sum(held),0),'unknown',coalesce(sum(unknown),0),
    'settled',coalesce(sum(settled),0),'risk',b.fixed_microyuan+coalesce(sum(held+unknown+settled),0))
    INTO totals FROM billing_control.exposures WHERE period_start=p_start;
  SELECT coalesce(jsonb_object_agg(pool,risk),'{}'::jsonb) INTO pool_totals FROM
    (SELECT pool,sum(held+unknown+settled) risk FROM billing_control.exposures WHERE period_start=p_start GROUP BY pool) t;
  RETURN to_jsonb(b)||totals||jsonb_build_object('pool_risk',pool_totals);
END $f$;
CREATE FUNCTION billing_api.lock_switches() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control AS $f$
DECLARE s billing_control.switches%ROWTYPE;
BEGIN
  SELECT * INTO STRICT s FROM billing_control.switches WHERE singleton FOR SHARE;
  RETURN jsonb_build_object('revision',s.revision,'flags',s.flags);
END $f$;
CREATE FUNCTION billing_api.sync_exposure(p_id uuid,p_admit boolean DEFAULT false) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control AS $f$
DECLARE r public.quota_reservations%ROWTYPE;b jsonb;h bigint:=0;u bigint:=0;s bigint:=0;old_risk bigint:=0;new_risk bigint;
BEGIN
  SELECT * INTO STRICT r FROM public.quota_reservations WHERE id=p_id;
  b:=billing_api.lock_budget(r.period_start);
  s:=coalesce(r.settled_microyuan,0);
  IF r.state IN ('reserved','submitted') THEN h:=greatest(r.reserved_microyuan-s,0);
  ELSIF r.state='unknown' THEN u:=greatest(r.reserved_microyuan-s,0);END IF;
  SELECT held+unknown+settled INTO old_risk FROM billing_control.exposures WHERE reservation_id=p_id;
  old_risk:=coalesce(old_risk,0);new_risk:=h+u+s;
  IF p_admit AND ((b->>'risk')::numeric+new_risk-old_risk>(b->>'budget_microyuan')::numeric-(b->>'safety_microyuan')::numeric
    OR coalesce((b->'pool_risk'->>r.pool)::numeric,0)+new_risk-old_risk>(b->'pools'->>r.pool)::numeric) THEN
    RAISE EXCEPTION 'BUDGET_BLOCKED' USING ERRCODE='P0001';
  END IF;
  INSERT INTO billing_control.exposures VALUES(p_id,r.period_start,r.pool,h,u,s)
    ON CONFLICT(reservation_id) DO UPDATE SET held=excluded.held,unknown=excluded.unknown,settled=excluded.settled;
  b:=billing_api.lock_budget(r.period_start);
  INSERT INTO billing_control.alerts(period_start,threshold)
    SELECT r.period_start,t FROM unnest(ARRAY[60,80,100]) t
    WHERE (b->>'risk')::numeric*100 >= (b->>'budget_microyuan')::numeric*t
    ON CONFLICT DO NOTHING;
END $f$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA billing_api FROM PUBLIC;
ALTER FUNCTION billing_api.lock_budget(timestamptz) OWNER TO classroom_billing_control;
ALTER FUNCTION billing_api.lock_switches() OWNER TO classroom_billing_control;
ALTER FUNCTION billing_api.sync_exposure(uuid,boolean) OWNER TO classroom_billing_control;
GRANT EXECUTE ON FUNCTION billing_api.lock_budget(timestamptz),billing_api.lock_switches(),billing_api.sync_exposure(uuid,boolean)
  TO classroom_web_runtime,classroom_billing_reconciler;

-- Operator SQL is deliberately a separate DB capability. The eventual HTTP
-- adapter must verify current Admin+MFA+reauth before entering this port.
CREATE FUNCTION billing_api.update_switches(p_actor uuid,p_op uuid,p_revision bigint,p_flags jsonb,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control AS $f$
DECLARE s billing_control.switches%ROWTYPE;old billing_control.audit%ROWTYPE;detail jsonb;
BEGIN
  SELECT * INTO STRICT s FROM billing_control.switches WHERE singleton FOR UPDATE;
  detail:=jsonb_build_object('revision',p_revision,'flags',p_flags);
  SELECT * INTO old FROM billing_control.audit WHERE action='switches' AND actor_id=p_actor AND op_id=p_op;
  IF FOUND THEN
    IF old.actor_id<>p_actor OR old.reason<>p_reason OR old.detail<>detail THEN RAISE EXCEPTION 'IDEMPOTENCY_MISMATCH';END IF;
    RETURN jsonb_build_object('revision',s.revision,'flags',s.flags);
  END IF;
  IF p_actor IS NULL OR p_reason IS NULL OR length(p_reason) NOT BETWEEN 1 AND 500 OR p_revision IS DISTINCT FROM s.revision
    OR p_flags IS NULL OR jsonb_typeof(p_flags)<>'object' THEN RAISE EXCEPTION 'invalid switch update';END IF;
  IF EXISTS(SELECT FROM jsonb_each(p_flags) WHERE NOT s.flags ? key OR jsonb_typeof(value)<>'boolean') THEN
    RAISE EXCEPTION 'invalid switch flags';END IF;
  UPDATE billing_control.switches SET flags=flags||p_flags,revision=revision+1 WHERE singleton RETURNING * INTO s;
  INSERT INTO billing_control.audit(id,op_id,actor_id,action,reason,detail)
    VALUES(gen_random_uuid(),p_op,p_actor,'switches',p_reason,detail);
  RETURN jsonb_build_object('revision',s.revision,'flags',s.flags);
END $f$;
ALTER FUNCTION billing_api.update_switches(uuid,uuid,bigint,jsonb,text) OWNER TO classroom_billing_control;
REVOKE ALL ON FUNCTION billing_api.update_switches(uuid,uuid,bigint,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION billing_api.update_switches(uuid,uuid,bigint,jsonb,text) TO classroom_billing_operator;

CREATE FUNCTION billing_api.open_period(p_start timestamptz,p_stage integer,p_actor uuid,p_op uuid,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control AS $f$
DECLARE amounts bigint[];finish timestamptz;detail jsonb;old billing_control.audit%ROWTYPE;
BEGIN
  -- The stage of an existing cycle is immutable. Stage changes apply to an
  -- explicitly opened future cycle until the product approves another policy.
  PERFORM 1 FROM billing_control.switches WHERE singleton FOR UPDATE;
  detail:=jsonb_build_object('period_start',p_start,'stage',p_stage);
  SELECT * INTO old FROM billing_control.audit WHERE action NOT IN ('switches','quota_adjustment') AND op_id=p_op;
  IF FOUND THEN
    IF old.actor_id<>p_actor OR old.reason<>p_reason OR old.detail<>detail THEN RAISE EXCEPTION 'IDEMPOTENCY_MISMATCH';END IF;
    RETURN;
  END IF;
  IF p_stage NOT IN (10,30) OR p_actor IS NULL OR p_reason IS NULL OR length(p_reason) NOT BETWEEN 1 AND 500
    OR p_start IS NULL OR p_start<>date_trunc('month',p_start AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' THEN
    RAISE EXCEPTION 'invalid budget cycle';END IF;
  finish:=((p_start AT TIME ZONE 'Asia/Shanghai')+interval '1 month') AT TIME ZONE 'Asia/Shanghai';
  IF p_stage=10 THEN amounts:=ARRAY[500,100,100,160,35,105];ELSE amounts:=ARRAY[2000,400,300,700,150,450];END IF;
  INSERT INTO billing_control.platform_budgets VALUES(p_start,finish,p_stage,amounts[1]*1000000,
    amounts[2]*1000000,amounts[3]*1000000,
    jsonb_build_object('asr',amounts[4]*1000000,'filetrans',amounts[5]*1000000,'ai',amounts[6]*1000000));
  INSERT INTO billing_control.audit(id,op_id,actor_id,action,reason,detail)
    VALUES(gen_random_uuid(),p_op,p_actor,'open_period',p_reason,detail);
END $f$;
ALTER FUNCTION billing_api.open_period(timestamptz,integer,uuid,uuid,text) OWNER TO classroom_billing_control;
REVOKE ALL ON FUNCTION billing_api.open_period(timestamptz,integer,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION billing_api.open_period(timestamptz,integer,uuid,uuid,text) TO classroom_billing_operator;

CREATE FUNCTION billing_api.guard_reservation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $f$
BEGIN
  IF (to_jsonb(NEW)-ARRAY['state','attempt_id','settled_microyuan']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['state','attempt_id','settled_microyuan'])
     OR (OLD.attempt_id IS NOT NULL AND NEW.attempt_id IS DISTINCT FROM OLD.attempt_id)
     OR (OLD.state='released' AND NEW IS DISTINCT FROM OLD)
     OR (OLD.state IN ('submitted','unknown','settled') AND NEW.state IN ('reserved','released'))
     OR (NEW.state IN ('submitted','unknown','settled') AND NEW.attempt_id IS NULL) THEN
    RAISE EXCEPTION 'invalid reservation transition' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $f$;
REVOKE ALL ON FUNCTION billing_api.guard_reservation() FROM PUBLIC;
CREATE TRIGGER reservation_identity BEFORE UPDATE ON public.quota_reservations
  FOR EACH ROW EXECUTE FUNCTION billing_api.guard_reservation();

-- Operator and HTTP share the same operation scope, including requests that
-- select different owners, quota rows or budget periods with one key.
CREATE FUNCTION billing_api.lock_adjustment_key(p_actor uuid,p_op uuid) RETURNS boolean
LANGUAGE plpgsql STRICT SECURITY DEFINER SET search_path=pg_catalog AS $f$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('quota_adjustment:'||p_actor::text||':'||p_op::text,0));
  RETURN EXISTS(SELECT 1 FROM billing_control.audit WHERE actor_id=p_actor AND action='quota_adjustment' AND op_id=p_op);
END $f$;
ALTER FUNCTION billing_api.lock_adjustment_key(uuid,uuid) OWNER TO classroom_billing_control;
REVOKE ALL ON FUNCTION billing_api.lock_adjustment_key(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION billing_api.adjust_quota(p_actor uuid,p_op uuid,p_quota uuid,p_delta bigint,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,billing_control AS $f$
DECLARE q public.quota_periods%ROWTYPE;old billing_control.audit%ROWTYPE;detail jsonb;
BEGIN
  IF p_actor IS NULL OR p_op IS NULL OR p_delta IS NULL OR p_delta<=0 OR p_reason IS NULL OR length(p_reason) NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'invalid quota adjustment';END IF;
  -- Caller sets only a verified target owner scope. Runtime has no EXECUTE.
  SELECT * INTO STRICT q FROM public.quota_periods WHERE id=p_quota;
  detail:=jsonb_build_object('quota_id',p_quota,'delta',p_delta);
  IF billing_api.lock_adjustment_key(p_actor,p_op) THEN
    SELECT * INTO STRICT old FROM billing_control.audit WHERE action='quota_adjustment' AND actor_id=p_actor AND op_id=p_op;
    IF old.reason<>p_reason OR old.detail<>detail THEN RAISE EXCEPTION 'IDEMPOTENCY_MISMATCH';END IF;
    RETURN;
  END IF;
  PERFORM billing_api.lock_budget(date_trunc('month',q.period_start AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai');
  SELECT * INTO STRICT q FROM public.quota_periods WHERE id=p_quota FOR UPDATE;
  -- The public quota uses JSON safe integers. Check headroom while holding the row lock.
  IF p_delta > 9007199254740991 - q.limit_value THEN
    RAISE EXCEPTION 'QUOTA_LIMIT_OUT_OF_RANGE' USING ERRCODE='22003';
  END IF;
  UPDATE public.quota_periods SET limit_value=limit_value+p_delta WHERE id=p_quota;
  INSERT INTO public.quota_adjustments(id,owner_id,quota_period_id,delta,reason,admin_actor_id,op_id)
    VALUES(gen_random_uuid(),q.owner_id,p_quota,p_delta,p_reason,p_actor,p_op);
  INSERT INTO billing_control.audit(id,op_id,actor_id,action,reason,detail)
    VALUES(gen_random_uuid(),p_op,p_actor,'quota_adjustment',p_reason,detail);
END $f$;
ALTER FUNCTION billing_api.adjust_quota(uuid,uuid,uuid,bigint,text) OWNER TO classroom_billing_control;
REVOKE ALL ON FUNCTION billing_api.adjust_quota(uuid,uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION billing_api.adjust_quota(uuid,uuid,uuid,bigint,text) TO classroom_billing_operator;
COMMENT ON FUNCTION billing_api.adjust_quota(uuid,uuid,uuid,bigint,text) IS 'classroom-admin-0.4.0-sql-2';
