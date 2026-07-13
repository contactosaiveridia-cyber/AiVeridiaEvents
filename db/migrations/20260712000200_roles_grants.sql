-- ============================================================================
-- Service role for the agents runtime: NOSUPERUSER + NOBYPASSRLS so every
-- query is subject to the tenant_isolation_* policies. The FastAPI service
-- connects as postgres/service and does `SET ROLE aiv_agent` +
-- `set_config('app.tenant_id', ..., true)` per transaction.
-- ============================================================================
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'aiv_agent') then
        create role aiv_agent nologin nosuperuser nobypassrls;
    end if;
end
$$;

grant usage on schema public to aiv_agent;

-- current_tenant() (SECURITY INVOKER) references auth.uid(): the agent role
-- needs to reach it or every RLS-protected query fails with 42501. On
-- Supabase the auth schema is managed; skip gracefully if not permitted.
do $$
begin
    grant usage on schema auth to aiv_agent;
    grant execute on function auth.uid() to aiv_agent;
exception when insufficient_privilege then
    raise notice 'auth schema grants skipped (managed environment)';
end
$$;
grant select, insert, update, delete on all tables in schema public to aiv_agent;
grant usage, select on all sequences in schema public to aiv_agent;

alter default privileges in schema public
    grant select, insert, update, delete on tables to aiv_agent;
alter default privileges in schema public
    grant usage, select on sequences to aiv_agent;

-- metricas_tenant is a materialized view (no RLS support): never expose it
-- directly to the agent role. Access goes through a SECURITY DEFINER function
-- that filters by current_tenant().
revoke all on metricas_tenant from aiv_agent;

create or replace function metricas_del_tenant()
returns setof metricas_tenant
language sql stable security definer
set search_path = public
as $$
    select * from metricas_tenant where tenant_id = current_tenant();
$$;

grant execute on function metricas_del_tenant() to aiv_agent;
