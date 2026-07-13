-- ============================================================================
-- Local/CI compatibility shim: Supabase provides auth.uid(); plain Postgres
-- (docker-compose dev, embedded test DBs) does not. current_tenant() in the
-- multitenant schema references auth.uid(), so it must exist before that
-- migration runs. On Supabase this block is a no-op.
-- ============================================================================
do $$
begin
    if not exists (select 1 from pg_namespace where nspname = 'auth') then
        create schema auth;
    end if;

    if not exists (
        select 1
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'auth' and p.proname = 'uid'
    ) then
        -- Mirrors Supabase: auth.uid() = sub del JWT. En local/tests se simula
        -- con: select set_config('request.jwt.claim.sub', '<uuid>', true);
        create function auth.uid() returns uuid
        language sql stable as
        $fn$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $fn$;
    end if;
end
$$;
