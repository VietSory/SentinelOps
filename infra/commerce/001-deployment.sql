-- Explicit deployment alternative to infra/local/init.sql. PostgreSQL 16 only.
-- Run as the authorized owner of a NEW sentinelops_commerce database, never
-- against a local testbed, control database or existing application database.
-- No LOGIN, password, catalog seed, Secret or cloud resource is created.
BEGIN;

DO $guard$
BEGIN
    IF current_database() <> 'sentinelops_commerce'
       OR current_setting('server_version_num')::integer / 10000 <> 16 THEN
        RAISE EXCEPTION 'commerce bootstrap requires PostgreSQL 16 database sentinelops_commerce';
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('commerce_owner', 'commerce_runtime'))
       OR EXISTS (SELECT FROM pg_namespace
                  WHERE nspname NOT IN ('public', 'information_schema') AND nspname NOT LIKE 'pg_%')
       OR EXISTS (SELECT FROM pg_class WHERE relnamespace = 'public'::regnamespace)
       OR EXISTS (SELECT FROM pg_proc WHERE pronamespace = 'public'::regnamespace)
       OR EXISTS (SELECT FROM pg_type WHERE typnamespace = 'public'::regnamespace) THEN
        RAISE EXCEPTION 'commerce bootstrap requires a fresh database and absent commerce roles';
    END IF;
END;
$guard$;

CREATE ROLE commerce_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE commerce_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Only the migration principal can SET the schema/table owner role.
GRANT commerce_owner TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;
REVOKE ALL ON DATABASE sentinelops_commerce FROM PUBLIC;
GRANT CONNECT ON DATABASE sentinelops_commerce TO commerce_runtime;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO commerce_owner;
GRANT USAGE ON SCHEMA public TO commerce_runtime;

SET LOCAL ROLE commerce_owner;
CREATE TABLE public.inventory (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0)
);
CREATE TABLE public.payments (
    payment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    currency CHAR(3) NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
-- Match the existing service SQL: Inventory reads stock; Payment inserts a
-- charge without RETURNING. Runtime has no catalog writes or payment reads.
GRANT SELECT ON public.inventory TO commerce_runtime;
GRANT INSERT ON public.payments TO commerce_runtime;
RESET ROLE;
COMMIT;
