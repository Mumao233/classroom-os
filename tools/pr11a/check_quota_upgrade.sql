-- Run against the existing billing database with the migration login before
-- copying its ledgers. This preflight reports only the number of invalid rows.
-- Keep writers quiesced between this check and the verified copy/cutover.
DO $preflight$
DECLARE invalid_rows bigint;
BEGIN
  IF row_security_active('public.quota_periods'::regclass) THEN
    RAISE EXCEPTION 'QUOTA_UPGRADE_REQUIRES_GLOBAL_VISIBILITY' USING ERRCODE='42501';
  END IF;
  LOCK TABLE public.quota_periods IN SHARE MODE;
  SELECT count(*) INTO invalid_rows FROM public.quota_periods WHERE limit_value>9007199254740991;
  IF invalid_rows>0 THEN
    RAISE EXCEPTION 'QUOTA_UPGRADE_REQUIRES_RECONCILIATION'
      USING ERRCODE='22003', DETAIL=format('%s quota rows exceed the HTTP safe integer limit',invalid_rows);
  END IF;
END $preflight$;
