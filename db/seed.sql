-- ============================================================================
-- Seed: tenant cero "Los Jazmines" (Trujillo, Perú).
-- Fixed UUIDs + on conflict do nothing => idempotent (safe to re-run).
-- Prices in PEN (S/).
-- ============================================================================

insert into tenants (id, nombre, ruc, plan, fee_por_reserva, whatsapp_phone_id, branding, timezone)
values (
    '11111111-1111-1111-1111-111111111111',
    'Los Jazmines',
    '20481234567',
    'starter',
    25.00,
    '510000000001',
    '{"tono": "cálido-comercial", "color": "#7C3AED", "ciudad": "Trujillo", "link_resena": "https://g.page/r/los-jazmines/review"}',
    'America/Lima'
)
on conflict (id) do nothing;

-- Owner (auth_uid placeholder: se reemplaza al vincular Supabase Auth real)
insert into tenant_users (id, tenant_id, auth_uid, rol)
values ('11111111-1111-1111-1111-1111111111aa',
        '11111111-1111-1111-1111-111111111111',
        '99999999-9999-9999-9999-999999999999', 'owner')
on conflict (id) do nothing;

-- ── 3 espacios ──────────────────────────────────────────────────────────────
insert into espacios (id, tenant_id, nombre, aforo_max) values
    ('11111111-1111-1111-1111-11111111e001', '11111111-1111-1111-1111-111111111111', 'Salón Principal', 150),
    ('11111111-1111-1111-1111-11111111e002', '11111111-1111-1111-1111-111111111111', 'Salón Jardín',     80),
    ('11111111-1111-1111-1111-11111111e003', '11111111-1111-1111-1111-111111111111', 'Sala Kids',        50)
on conflict (id) do nothing;

-- ── 4 paquetes ──────────────────────────────────────────────────────────────
insert into paquetes (id, tenant_id, nombre, descripcion, precio_base, incluye) values
    ('11111111-1111-1111-1111-11111111b001'::uuid, '11111111-1111-1111-1111-111111111111',
     'Fiesta Básica', 'Hasta 50 invitados, 4 horas de local', 1800.00,
     '["local 4h", "mesas y sillas", "sonido básico", "limpieza"]'),
    ('11111111-1111-1111-1111-11111111b002'::uuid, '11111111-1111-1111-1111-111111111111',
     'Fiesta Clásica', 'Hasta 80 invitados, 5 horas, decoración estándar', 2800.00,
     '["local 5h", "decoración estándar", "torta 30p", "sonido y luces", "mozo"]'),
    ('11111111-1111-1111-1111-11111111b003'::uuid, '11111111-1111-1111-1111-111111111111',
     'Fiesta Premium', 'Hasta 120 invitados, 6 horas, todo incluido', 4500.00,
     '["local 6h", "decoración temática", "torta 50p", "animación 2h", "hora loca", "catering", "fotografía"]'),
    ('11111111-1111-1111-1111-11111111b004'::uuid, '11111111-1111-1111-1111-111111111111',
     'Temático Kids Total', 'Hasta 60 invitados en Sala Kids, personaje a elección', 3500.00,
     '["local 5h", "decoración temática a elección", "torta temática 40p", "animación 3h con personaje", "juegos inflables"]')
on conflict (id) do nothing;

-- ── Reglas de precio (evaluadas por código determinista, jamás por el LLM) ──
insert into reglas_precio (id, tenant_id, tipo, condicion, prioridad) values
    ('11111111-1111-1111-1111-11111111c001'::uuid, '11111111-1111-1111-1111-111111111111',
     'temporada',    '{"meses": [12, 1], "factor": 1.20, "nota": "temporada alta navideña"}', 10),
    ('11111111-1111-1111-1111-11111111c002'::uuid, '11111111-1111-1111-1111-111111111111',
     'temporada',    '{"meses": [7], "factor": 1.10, "nota": "fiestas patrias / vacaciones"}', 20),
    ('11111111-1111-1111-1111-11111111c003'::uuid, '11111111-1111-1111-1111-111111111111',
     'dia_semana',   '{"dow": [6], "factor": 1.15, "nota": "sábado"}', 30),
    ('11111111-1111-1111-1111-11111111c004'::uuid, '11111111-1111-1111-1111-111111111111',
     'dia_semana',   '{"dow": [0], "factor": 1.10, "nota": "domingo"}', 31),
    ('11111111-1111-1111-1111-11111111c005'::uuid, '11111111-1111-1111-1111-111111111111',
     'dia_semana',   '{"dow": [1, 2, 3, 4], "factor": 0.90, "nota": "lunes a jueves"}', 32),
    ('11111111-1111-1111-1111-11111111c006'::uuid, '11111111-1111-1111-1111-111111111111',
     'anticipacion', '{"min_dias": 90, "factor": 0.95, "nota": "reserva anticipada 3+ meses"}', 40),
    ('11111111-1111-1111-1111-11111111c007'::uuid, '11111111-1111-1111-1111-111111111111',
     'aforo',        '{"min_aforo": 100, "factor": 1.10, "nota": "eventos grandes"}', 50)
on conflict (id) do nothing;

-- ── 10 proveedores ──────────────────────────────────────────────────────────
insert into proveedores (id, tenant_id, nombre, rubro, telefono, confiabilidad) values
    ('11111111-1111-1111-1111-11111111a001'::uuid, '11111111-1111-1111-1111-111111111111', 'Tortas Dulce Sueño',          'torta',      '+51944000001', 0.98),
    ('11111111-1111-1111-1111-11111111a002'::uuid, '11111111-1111-1111-1111-111111111111', 'Pastelería La Norteñita',     'torta',      '+51944000002', 0.92),
    ('11111111-1111-1111-1111-11111111a003'::uuid, '11111111-1111-1111-1111-111111111111', 'Decoraciones Fantasía',       'decoracion', '+51944000003', 0.95),
    ('11111111-1111-1111-1111-11111111a004'::uuid, '11111111-1111-1111-1111-111111111111', 'Globos y Detalles Trujillo',  'decoracion', '+51944000004', 0.90),
    ('11111111-1111-1111-1111-11111111a005'::uuid, '11111111-1111-1111-1111-111111111111', 'Animaciones Happy Kids',      'animacion',  '+51944000005', 0.97),
    ('11111111-1111-1111-1111-11111111a006'::uuid, '11111111-1111-1111-1111-111111111111', 'Show Infantil Pin Pin',       'animacion',  '+51944000006', 0.88),
    ('11111111-1111-1111-1111-11111111a007'::uuid, '11111111-1111-1111-1111-111111111111', 'Catering El Buen Sabor',      'catering',   '+51944000007', 0.96),
    ('11111111-1111-1111-1111-11111111a008'::uuid, '11111111-1111-1111-1111-111111111111', 'Bocaditos Doña Carmen',       'catering',   '+51944000008', 0.93),
    ('11111111-1111-1111-1111-11111111a009'::uuid, '11111111-1111-1111-1111-111111111111', 'Foto & Video Momentos',       'fotografia', '+51944000009', 0.94),
    ('11111111-1111-1111-1111-11111111a010'::uuid, '11111111-1111-1111-1111-111111111111', 'Sonido y Luces ProEvent',     'otro',       '+51944000010', 0.91)
on conflict (id) do nothing;

refresh materialized view metricas_tenant;
