-- Experimental portable transfers. The runtime sees only its current owner.
DO $role$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='classroom_web_portable_maintainer') THEN
        CREATE ROLE classroom_web_portable_maintainer NOLOGIN NOINHERIT NOSUPERUSER
            NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname='classroom_web_portable_maintainer'
        AND (rolcanlogin OR rolinherit OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication))
        OR EXISTS (SELECT FROM pg_auth_members WHERE roleid='classroom_web_portable_maintainer'::regrole
                  OR member='classroom_web_portable_maintainer'::regrole) THEN
        RAISE EXCEPTION 'unsafe portable maintainer role';
    END IF;
END
$role$;

CREATE TABLE public.portable_transfers (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES public.users(id),
    kind text NOT NULL CHECK (kind IN ('import','export')),
    op_id uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
    reserved_bytes bigint NOT NULL CHECK (reserved_bytes BETWEEN 1 AND 500000000),
    state text NOT NULL CHECK (state IN ('uploading','verified','building','published','closed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    access_until timestamptz,
    erased_at timestamptz,
    destination_course_id uuid,
    archive_sha256 text CHECK (archive_sha256 ~ '^[a-f0-9]{64}$'),
    mime_type text CHECK (mime_type IN ('application/zip','application/json')),
    policy_version text NOT NULL CHECK (policy_version='tbd06-v2'),
    CHECK ((kind='import')=(archive_sha256 IS NOT NULL AND mime_type IS NOT NULL)),
    UNIQUE (owner_id,id), UNIQUE (owner_id,kind,op_id),
    FOREIGN KEY (owner_id,destination_course_id) REFERENCES public.courses(owner_id,id),
    CHECK (erased_at IS NULL OR state='closed'),
    CHECK (state='building' OR access_until IS NOT NULL)
);
CREATE INDEX portable_unerased ON public.portable_transfers(kind)
    WHERE erased_at IS NULL;
CREATE INDEX portable_expiry ON public.portable_transfers(access_until)
    WHERE state<>'closed';

CREATE TABLE public.portable_transfer_media (
    owner_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    part_number integer NOT NULL CHECK (part_number BETWEEN 0 AND 31),
    PRIMARY KEY (transfer_id,part_number), UNIQUE (asset_id),
    FOREIGN KEY (owner_id,transfer_id) REFERENCES public.portable_transfers(owner_id,id),
    FOREIGN KEY (owner_id,asset_id) REFERENCES public.media_assets(owner_id,id)
);

CREATE TABLE public.portable_export_sources (
    owner_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    class_session_id uuid NOT NULL,
    PRIMARY KEY (transfer_id,class_session_id),
    FOREIGN KEY (owner_id,transfer_id) REFERENCES public.portable_transfers(owner_id,id),
    FOREIGN KEY (owner_id,class_session_id) REFERENCES public.class_sessions(owner_id,id)
);

CREATE TABLE public.portable_slots (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    op_id uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
    state text NOT NULL CHECK (state IN ('running','waiting','completed','failed','cancelled','expired')),
    worker_id uuid,
    fence bigint NOT NULL DEFAULT 1 CHECK (fence >= 1),
    deadline timestamptz NOT NULL,
    finished_at timestamptz,
    UNIQUE (owner_id,op_id),
    FOREIGN KEY (owner_id,transfer_id) REFERENCES public.portable_transfers(owner_id,id),
    CHECK ((state='running')=(worker_id IS NOT NULL)),
    CHECK ((state IN ('waiting','running'))=(finished_at IS NULL))
);
CREATE UNIQUE INDEX portable_one_slot_per_owner ON public.portable_slots(owner_id)
    WHERE state IN ('running','waiting');
CREATE INDEX portable_slot_deadline ON public.portable_slots(deadline)
    WHERE state IN ('running','waiting');

DO $policy$
DECLARE n text;
BEGIN
    FOREACH n IN ARRAY ARRAY['portable_transfers','portable_transfer_media','portable_export_sources','portable_slots'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',n);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',n);
        EXECUTE format('CREATE POLICY portable_owner ON public.%I TO classroom_web_runtime
            USING (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
                AND public.classroom_web_owner_active())
            WITH CHECK (owner_id=nullif(current_setting(''app.owner_id'',true),'''')::uuid
                AND public.classroom_web_owner_active())', n);
        EXECUTE format('CREATE POLICY portable_maintainer ON public.%I TO classroom_web_portable_maintainer
            USING (true) WITH CHECK (true)', n);
        EXECUTE format('GRANT SELECT,INSERT ON public.%I TO classroom_web_runtime',n);
        EXECUTE format('GRANT SELECT ON public.%I TO classroom_web_portable_maintainer',n);
    END LOOP;
END
$policy$;

GRANT UPDATE (state, access_until, destination_course_id) ON public.portable_transfers TO classroom_web_runtime;
GRANT UPDATE (state, worker_id, fence, deadline, finished_at) ON public.portable_slots TO classroom_web_runtime;
GRANT UPDATE (state, access_until, erased_at) ON public.portable_transfers TO classroom_web_portable_maintainer;
GRANT UPDATE (state, worker_id, fence, finished_at) ON public.portable_slots TO classroom_web_portable_maintainer;
GRANT SELECT (id, owner_id, kind, status, expires_at), UPDATE (expires_at)
    ON public.media_assets TO classroom_web_portable_maintainer;
CREATE POLICY portable_linked_media ON public.media_assets TO classroom_web_portable_maintainer
    USING (kind IN ('import_package','export') AND EXISTS (SELECT FROM public.portable_transfer_media p
                  WHERE p.asset_id=media_assets.id AND p.owner_id=media_assets.owner_id))
    WITH CHECK (kind IN ('import_package','export') AND EXISTS (SELECT FROM public.portable_transfer_media p
                       WHERE p.asset_id=media_assets.id AND p.owner_id=media_assets.owner_id));
GRANT SELECT (owner_id, resource_type, resource_id, request_id)
    ON public.deletion_tombstones TO classroom_web_portable_maintainer;
GRANT SELECT (id, owner_id, access_revoked_at) ON public.deletion_requests TO classroom_web_portable_maintainer;
CREATE POLICY portable_revocation_request ON public.deletion_requests FOR SELECT TO classroom_web_portable_maintainer USING (true);
CREATE POLICY portable_revocation_clock ON public.deletion_tombstones FOR SELECT TO classroom_web_portable_maintainer
    USING (resource_type IN ('class_session','course','semester','account'));
GRANT SELECT (id, owner_id, course_id) ON public.class_sessions TO classroom_web_portable_maintainer;
GRANT SELECT (id, owner_id, semester_id) ON public.courses TO classroom_web_portable_maintainer;
CREATE POLICY portable_source_parent ON public.class_sessions FOR SELECT TO classroom_web_portable_maintainer USING (true);
CREATE POLICY portable_source_parent ON public.courses FOR SELECT TO classroom_web_portable_maintainer USING (true);

-- Admission holds this lock until its transaction commits. The same routine
-- is also callable by the deletion worker when no owner is online.
CREATE FUNCTION public.classroom_web_portable_gate()
RETURNS TABLE(slots bigint, import_bytes bigint, export_bytes bigint, alerts bigint, incidents bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $gate$
DECLARE ts timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(41006,21);
    ts := clock_timestamp();

    WITH expired AS (
        UPDATE public.portable_slots s
        SET state=CASE WHEN s.state='running' THEN 'failed' ELSE 'expired' END,
            finished_at=s.deadline, worker_id=NULL, fence=s.fence+1
        WHERE s.state IN ('running','waiting') AND s.deadline<=ts
        RETURNING s.transfer_id, s.state, s.finished_at
    )
    UPDATE public.portable_transfers t SET state='closed',
        access_until=least(t.access_until, e.finished_at)
    FROM expired e WHERE e.transfer_id=t.id AND e.state='failed' AND t.state<>'closed';

    WITH cutoffs AS (
        SELECT t.id, min(r.access_revoked_at) AS deadline
        FROM public.portable_transfers t
        LEFT JOIN public.portable_export_sources s ON (s.owner_id,s.transfer_id)=(t.owner_id,t.id)
        LEFT JOIN public.class_sessions c ON (c.owner_id,c.id)=(s.owner_id,s.class_session_id)
        LEFT JOIN public.courses p ON p.owner_id=t.owner_id AND p.id=coalesce(c.course_id,t.destination_course_id)
        JOIN public.deletion_tombstones d ON d.owner_id=t.owner_id AND (
            (d.resource_type='account' AND d.resource_id=t.owner_id)
            OR (d.resource_type='class_session' AND d.resource_id=c.id)
            OR (d.resource_type='course' AND d.resource_id=p.id)
            OR (d.resource_type='semester' AND d.resource_id=p.semester_id))
        JOIN public.deletion_requests r ON (r.owner_id,r.id)=(d.owner_id,d.request_id)
        WHERE t.erased_at IS NULL
        GROUP BY t.id
    )
    UPDATE public.portable_transfers t SET state='closed', access_until=least(t.access_until,c.deadline)
    FROM cutoffs c WHERE t.id=c.id AND (t.state<>'closed' OR t.access_until>c.deadline);

    UPDATE public.portable_transfers t SET state='closed'
    WHERE t.state<>'closed' AND t.access_until<=ts;
    UPDATE public.portable_slots s SET state='cancelled', worker_id=NULL, fence=s.fence+1,
        finished_at=t.access_until
    FROM public.portable_transfers t
    WHERE t.id=s.transfer_id AND t.state='closed' AND s.state IN ('running','waiting');

    UPDATE public.media_assets a SET expires_at=least(a.expires_at,t.access_until)
    FROM public.portable_transfer_media l JOIN public.portable_transfers t ON t.id=l.transfer_id
    WHERE a.id=l.asset_id AND a.owner_id=l.owner_id AND t.state='closed'
        AND a.status IN ('staging','verified','committed')
        AND (a.expires_at IS NULL OR a.expires_at>t.access_until);

    UPDATE public.portable_transfers t SET erased_at=ts
    WHERE t.state='closed' AND t.erased_at IS NULL AND NOT EXISTS (
        SELECT FROM public.portable_transfer_media l JOIN public.media_assets a
            ON (a.owner_id,a.id)=(l.owner_id,l.asset_id)
        WHERE (l.owner_id,l.transfer_id)=(t.owner_id,t.id) AND a.status<>'deleted'
    );

    RETURN QUERY SELECT
        (SELECT count(*) FROM public.portable_slots s WHERE s.state IN ('running','waiting')),
        coalesce(sum(t.reserved_bytes) FILTER (WHERE t.kind='import'),0)::bigint,
        coalesce(sum(t.reserved_bytes) FILTER (WHERE t.kind='export'),0)::bigint,
        count(*) FILTER (WHERE t.state='closed' AND t.access_until<=ts-interval '24 hours'),
        count(*) FILTER (WHERE t.state='closed' AND t.access_until<=ts-interval '72 hours')
    FROM public.portable_transfers t WHERE t.erased_at IS NULL;
END
$gate$;

GRANT CREATE, USAGE ON SCHEMA public TO classroom_web_portable_maintainer;
ALTER FUNCTION public.classroom_web_portable_gate() OWNER TO classroom_web_portable_maintainer;
REVOKE CREATE ON SCHEMA public FROM classroom_web_portable_maintainer;
REVOKE ALL ON FUNCTION public.classroom_web_portable_gate() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_portable_gate() TO classroom_web_deletion_worker;
CREATE FUNCTION public.classroom_web_portable_admit()
RETURNS TABLE(slots bigint, import_bytes bigint, export_bytes bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $admit$
BEGIN
    IF NOT public.classroom_web_owner_active() THEN
        RAISE EXCEPTION 'portable admission requires an active owner' USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT g.slots, g.import_bytes, g.export_bytes FROM public.classroom_web_portable_gate() g;
END
$admit$;
GRANT EXECUTE ON FUNCTION public.classroom_web_owner_active() TO classroom_web_portable_maintainer;
GRANT CREATE ON SCHEMA public TO classroom_web_portable_maintainer;
ALTER FUNCTION public.classroom_web_portable_admit() OWNER TO classroom_web_portable_maintainer;
REVOKE CREATE ON SCHEMA public FROM classroom_web_portable_maintainer;
REVOKE ALL ON FUNCTION public.classroom_web_portable_admit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.classroom_web_portable_admit() TO classroom_web_runtime;
