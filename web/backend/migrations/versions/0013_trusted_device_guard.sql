-- trustDevice remains unapproved. Close the 0011 direct-DML gap in one migration transaction.
-- Wrapped keys and key epochs remain intact for the separately gated recovery policy.
LOCK TABLE public.devices IN ACCESS EXCLUSIVE MODE;
UPDATE public.devices SET trusted=false WHERE trusted;
ALTER TABLE public.devices ADD CONSTRAINT devices_trusted_check CHECK (NOT trusted);
