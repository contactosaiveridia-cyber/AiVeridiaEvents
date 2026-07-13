-- ============================================================================
-- Audit trail (non-negotiable rule 5: every date release must be audited).
-- A trigger on reservas records every state transition; releases/cancellations
-- therefore always leave a row regardless of which code path caused them.
-- ============================================================================
create table eventos_auditoria (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    entidad    text not null,                        -- 'reserva', 'cotizacion', ...
    entidad_id uuid,
    accion     text not null,                        -- 'estado: hold -> cancelada'
    detalle    jsonb not null default '{}',
    actor      text not null default current_user,
    creado_en  timestamptz not null default now()
);
create index idx_auditoria_tenant on eventos_auditoria (tenant_id, entidad, creado_en);

alter table eventos_auditoria enable row level security;
create policy tenant_isolation_eventos_auditoria on eventos_auditoria
    using (tenant_id = current_tenant())
    with check (tenant_id = current_tenant());

-- SECURITY DEFINER: the audit insert must never fail due to RLS context of the
-- caller (e.g. maintenance done by the service role without app.tenant_id).
create or replace function audit_reserva_estado() returns trigger
language plpgsql security definer
set search_path = public
as $$
begin
    if new.estado is distinct from old.estado then
        insert into eventos_auditoria (tenant_id, entidad, entidad_id, accion, detalle)
        values (
            new.tenant_id, 'reserva', new.id,
            format('estado: %s -> %s', old.estado, new.estado),
            jsonb_build_object(
                'espacio_id', new.espacio_id,
                'lead_id', new.lead_id,
                'inicio', new.inicio,
                'fin', new.fin
            )
        );
    end if;
    return new;
end;
$$;

create trigger trg_audit_reserva_estado
    after update on reservas
    for each row execute function audit_reserva_estado();
