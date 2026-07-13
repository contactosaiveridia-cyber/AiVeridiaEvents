-- ============================================================================
-- Webhook idempotency (non-negotiable rule 4): Meta and the payment gateways
-- retry deliveries; every event is registered once by (fuente, evento_id) and
-- duplicates are dropped at the edge BEFORE touching business state.
--
-- Edge-infrastructure table: it is written by the service connection before
-- tenant resolution, so it carries no RLS; the agent role cannot touch it.
-- ============================================================================
create table webhook_eventos (
    id          bigint generated always as identity primary key,
    fuente      text not null,               -- 'whatsapp' | 'culqi' | 'mercadopago' | 'scheduler'
    evento_id   text not null,               -- id de mensaje / transacción
    tenant_id   uuid,                        -- para observabilidad (puede ser null)
    payload     jsonb not null default '{}',
    recibido_en timestamptz not null default now(),
    unique (fuente, evento_id)
);
create index idx_webhook_eventos_tenant on webhook_eventos (tenant_id, recibido_en);

revoke all on webhook_eventos from aiv_agent;
