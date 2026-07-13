-- ============================================================================
-- aiVeridia Events — Esquema multi-tenant (Supabase / PostgreSQL 15+)
-- Patrón: pool model (una BD, RLS por tenant_id) — mismo patrón que
-- aiVeridia Academy, lo que permite reutilizar el middleware de tenancy.
-- ============================================================================
create extension if not exists "pgcrypto";
create extension if not exists "btree_gist";   -- anti doble-reserva
create extension if not exists "vector";       -- pgvector: memoria y FAQ RAG

-- ----------------------------------------------------------------------------
-- 1. TENANCY
-- ----------------------------------------------------------------------------
create table tenants (
    id           uuid primary key default gen_random_uuid(),
    nombre       text not null,                      -- "Los Jazmines"
    ruc          text unique,
    plan         text not null default 'starter'
                 check (plan in ('starter','pro','premium')),
    -- Fee por resultado del modelo híbrido AaaS:
    fee_por_reserva numeric(8,2) not null default 20.00,
    whatsapp_phone_id text,                          -- WhatsApp Business API
    branding     jsonb not null default '{}',        -- logo, colores, tono
    timezone     text not null default 'America/Lima',
    creado_en    timestamptz not null default now()
);

create table tenant_users (                          -- dueños y staff del salón
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    auth_uid   uuid not null,                        -- auth.users de Supabase
    rol        text not null check (rol in ('owner','staff')),
    unique (tenant_id, auth_uid)
);

-- ----------------------------------------------------------------------------
-- 2. CATÁLOGO COMERCIAL (insumos del businessRuleTask a2_cotizar)
-- ----------------------------------------------------------------------------
create table espacios (                              -- un salón puede tener varios
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    nombre     text not null,                        -- "Salón principal", "Terraza"
    aforo_max  int  not null check (aforo_max > 0)
);

create table paquetes (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references tenants(id) on delete cascade,
    nombre      text not null,                       -- "Básico", "Premium", "Temático"
    descripcion text,
    precio_base numeric(10,2) not null,
    incluye     jsonb not null default '[]',         -- ["torta 20p","animación 2h"]
    activo      boolean not null default true
);

create table reglas_precio (                         -- evaluadas por código, no LLM
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    tipo       text not null check (tipo in
               ('temporada','dia_semana','aforo','anticipacion')),
    condicion  jsonb not null,     -- {"dow":[6],"factor":1.15} sábado +15%
    prioridad  int not null default 100
);

-- ----------------------------------------------------------------------------
-- 3. FUNNEL COMERCIAL (grafo P1)
-- ----------------------------------------------------------------------------
create table leads (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null references tenants(id) on delete cascade,
    canal         text not null check (canal in ('whatsapp','facebook','instagram','web','walk_in')),
    telefono      text,
    nombre        text,
    estado        text not null default 'nuevo' check (estado in
                  ('nuevo','calificado','cotizado','seguimiento',
                   'nurturing','convertido','perdido')),
    calificacion  jsonb,            -- Calificacion (fecha, aforo, tipo, agasajado)
    creado_en     timestamptz not null default now()
);
create index idx_leads_tenant_estado on leads (tenant_id, estado);

create table conversaciones (                        -- checkpoint humano-legible
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references tenants(id) on delete cascade,
    lead_id     uuid not null references leads(id) on delete cascade,
    thread_id   text not null unique,                -- "{tenant_id}:{lead_id}"
    mensajes    jsonb not null default '[]',
    actualizado timestamptz not null default now()
);

create table cotizaciones (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null references tenants(id) on delete cascade,
    lead_id       uuid not null references leads(id),
    paquete_id    uuid not null references paquetes(id),
    precio_lista  numeric(10,2) not null,
    descuento_pct numeric(5,2) not null default 0,
    precio_final  numeric(10,2) not null,
    aprobada_por  uuid references tenant_users(id),  -- freno humano si > umbral
    valida_hasta  timestamptz not null,
    creado_en     timestamptz not null default now()
);

create table reservas (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    uuid not null references tenants(id) on delete cascade,
    lead_id      uuid not null references leads(id),
    espacio_id   uuid not null references espacios(id),
    cotizacion_id uuid references cotizaciones(id),
    inicio       timestamptz not null,
    fin          timestamptz not null,
    estado       text not null default 'hold' check (estado in
                 ('hold','confirmada','ejecutada','cancelada')),
    contrato_url text,
    creado_en    timestamptz not null default now(),
    check (fin > inicio),
    -- ANTI DOBLE-RESERVA: dos reservas activas no pueden solaparse en un espacio.
    -- Es el "candado" físico que el agente A4 jamás puede violar.
    constraint reservas_sin_solape exclude using gist (
        espacio_id with =,
        tstzrange(inicio, fin) with &&
    ) where (estado in ('hold','confirmada'))
);

create table pagos (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references tenants(id) on delete cascade,
    reserva_id  uuid not null references reservas(id),
    concepto    text not null check (concepto in ('separacion','cuota','saldo')),
    monto       numeric(10,2) not null,
    medio       text check (medio in ('yape','plin','tarjeta','transferencia','efectivo')),
    pasarela_ref text,                               -- id de Culqi/MercadoPago
    estado      text not null default 'pendiente'
                check (estado in ('pendiente','pagado','vencido')),
    vence_en    timestamptz,
    pagado_en   timestamptz
);
create index idx_pagos_pendientes on pagos (tenant_id, estado, vence_en);

-- ----------------------------------------------------------------------------
-- 4. OPERACIÓN Y FIDELIZACIÓN (grafo P2)
-- ----------------------------------------------------------------------------
create table proveedores (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    nombre     text not null,
    rubro      text not null check (rubro in
               ('torta','decoracion','animacion','catering','fotografia','otro')),
    telefono   text,
    confiabilidad numeric(3,2) default 1.0           -- alimenta al orquestador A5
);

create table ordenes_proveedor (                     -- multi-instancia del BPMN
    id           uuid primary key default gen_random_uuid(),
    tenant_id    uuid not null references tenants(id) on delete cascade,
    reserva_id   uuid not null references reservas(id) on delete cascade,
    proveedor_id uuid not null references proveedores(id),
    detalle      jsonb not null,
    estado       text not null default 'notificado' check (estado in
                 ('notificado','confirmado','escalado','sustituido','cumplido')),
    confirmado_en timestamptz
);

create table nps_respuestas (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    reserva_id uuid not null references reservas(id),
    score      int check (score between 0 and 10),
    comentario text,
    creado_en  timestamptz not null default now()
);

create table campanias_renovacion (                  -- el moat de recurrencia anual
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null references tenants(id) on delete cascade,
    reserva_origen uuid not null references reservas(id),
    agasajado     text,
    disparar_en   timestamptz not null,              -- +10 meses (EventBridge)
    estado        text not null default 'programada'
                  check (estado in ('programada','enviada','convertida','descartada'))
);

-- ----------------------------------------------------------------------------
-- 5. CONOCIMIENTO POR TENANT (RAG del agente A1 con pgvector)
-- ----------------------------------------------------------------------------
create table conocimiento (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  uuid not null references tenants(id) on delete cascade,
    contenido  text not null,                        -- FAQ, reglas del local, tips
    embedding  vector(1024),                         -- Titan/Cohere embed
    fuente     text
);
create index idx_conocimiento_vec on conocimiento
    using hnsw (embedding vector_cosine_ops);

-- ----------------------------------------------------------------------------
-- 6. ROW LEVEL SECURITY — aislamiento estricto por tenant
-- ----------------------------------------------------------------------------
-- Los agentes acceden con un rol de servicio que fija el tenant por conexión:
--   select set_config('app.tenant_id', '<uuid>', true);
-- El dashboard React usa el JWT de Supabase (tenant_users).
create or replace function current_tenant() returns uuid
language sql stable as $$
    select coalesce(
        nullif(current_setting('app.tenant_id', true), '')::uuid,
        (select tenant_id from tenant_users
         where auth_uid = auth.uid() limit 1)
    );
$$;

do $$
declare t text;
begin
    foreach t in array array[
        'espacios','paquetes','reglas_precio','leads','conversaciones',
        'cotizaciones','reservas','pagos','proveedores','ordenes_proveedor',
        'nps_respuestas','campanias_renovacion','conocimiento']
    loop
        execute format('alter table %I enable row level security', t);
        execute format(
            'create policy tenant_isolation_%1$s on %1$s
             using (tenant_id = current_tenant())
             with check (tenant_id = current_tenant())', t);
    end loop;
end $$;

alter table tenants enable row level security;
create policy tenant_self on tenants
    using (id = current_tenant());

-- ----------------------------------------------------------------------------
-- 7. MÉTRICAS A9 (dashboard del dueño)
-- ----------------------------------------------------------------------------
create materialized view metricas_tenant as
select
    l.tenant_id,
    date_trunc('month', l.creado_en)                    as mes,
    count(*)                                            as leads,
    count(*) filter (where l.estado = 'convertido')     as convertidos,
    round(100.0 * count(*) filter (where l.estado = 'convertido')
          / nullif(count(*),0), 1)                      as tasa_conversion_pct,
    coalesce(sum(c.precio_final)
          filter (where l.estado = 'convertido'), 0)    as ingresos_generados
from leads l
left join cotizaciones c on c.lead_id = l.id
group by 1, 2;
create unique index on metricas_tenant (tenant_id, mes);
-- refresh materialized view concurrently metricas_tenant;  -- pg_cron nocturno
