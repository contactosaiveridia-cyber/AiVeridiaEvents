-- ============================================================================
-- current_tenant() como PL/pgSQL con corto-circuito real.
--
-- La versión SQL original se "inlinea" en el plan de cada consulta, lo que
-- obliga al planner a resolver auth.uid() SIEMPRE — incluso cuando el GUC de
-- tenant ya está fijado. En Postgres gestionado (Supabase) el rol del runtime
-- (aiv_agent) no tiene USAGE sobre el schema auth, así que ese inlining rompe
-- toda consulta con "permission denied for schema auth".
--
-- En PL/pgSQL la función NO se inlinea y el IF corta antes de tocar auth.uid():
--   - camino del agente  -> app.tenant_id fijado -> retorna sin ver auth.
--   - camino del dashboard -> sin GUC, corriendo como 'authenticated' (que sí
--     tiene acceso a auth) -> resuelve auth.uid() normalmente.
-- ============================================================================
create or replace function current_tenant() returns uuid
language plpgsql stable as $$
declare
    t uuid;
begin
    t := nullif(current_setting('app.tenant_id', true), '')::uuid;
    if t is not null then
        return t;
    end if;
    return (select tenant_id from tenant_users
             where auth_uid = auth.uid() limit 1);
end;
$$;
