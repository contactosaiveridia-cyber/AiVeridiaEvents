-- ============================================================================
-- F7: capa de datos del dashboard del dueño.
--
-- 1. Bandeja de aprobaciones: los interrupt() de LangGraph viven en el
--    checkpointer (no consultables); cada interrupt pendiente se refleja aquí
--    para que el dashboard la lea como una tabla normal con RLS. La acción de
--    responder pasa por la API de agents (que reanuda el thread).
-- 2. Roles de Supabase (authenticated/anon) para entornos locales, con los
--    mismos grants mínimos que usa el dashboard: SELECT + RLS. En Supabase
--    real los roles ya existen y el DO los salta.
-- ============================================================================
create table aprobaciones (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references tenants(id) on delete cascade,
    tipo        text not null check (tipo in
                ('aprobacion_descuento', 'proveedor_sin_confirmar')),
    referencia  text not null,               -- lead_id (P1) o booking_id (P2)
    payload     jsonb not null default '{}',
    estado      text not null default 'pendiente'
                check (estado in ('pendiente', 'aprobada', 'rechazada')),
    creado_en   timestamptz not null default now(),
    resuelto_en timestamptz
);
create unique index idx_aprobaciones_pendiente
    on aprobaciones (tenant_id, tipo, referencia) where (estado = 'pendiente');
create index idx_aprobaciones_tenant on aprobaciones (tenant_id, estado, creado_en);

alter table aprobaciones enable row level security;
create policy tenant_isolation_aprobaciones on aprobaciones
    using (tenant_id = current_tenant())
    with check (tenant_id = current_tenant());

grant select, insert, update on aprobaciones to aiv_agent;

-- ── Roles del dashboard (compat local con Supabase) ─────────────────────────
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin nosuperuser nobypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin nosuperuser nobypassrls;
    end if;
end
$$;

grant usage on schema public to authenticated;
do $$
begin
    grant usage on schema auth to authenticated;
    grant execute on function auth.uid() to authenticated;
exception when insufficient_privilege then
    raise notice 'auth grants skipped (managed environment)';
end
$$;

-- El dueño LEE su embudo, calendario, bandeja y métricas; toda escritura de
-- negocio pasa por los agentes. RLS filtra por tenant vía tenant_users.
grant select on tenants, tenant_users, espacios, paquetes, leads, cotizaciones,
                reservas, pagos, proveedores, ordenes_proveedor, nps_respuestas,
                campanias_renovacion, aprobaciones
    to authenticated;
grant execute on function metricas_del_tenant() to authenticated;

-- tenant_users no tenía política (el schema base no la incluye): el dueño
-- necesita resolver su propio tenant al iniciar sesión.
-- IMPORTANTE: esta política NO puede referenciar current_tenant() — esa
-- función consulta tenant_users y la RLS se volvería infinitamente recursiva.
alter table tenant_users enable row level security;
create policy self_tenant_users on tenant_users
    using (auth_uid = auth.uid());
