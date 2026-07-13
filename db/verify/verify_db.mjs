// Verifica migraciones + seed + RLS + anti doble-reserva + auditoría contra
// un Postgres embebido (PGlite), replicando las aserciones de la suite pytest.
import { PGlite } from '@electric-sql/pglite'
import { vector } from '@electric-sql/pglite-pgvector'
import { btree_gist } from '@electric-sql/pglite/contrib/btree_gist'
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto'
import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const db = new PGlite({ extensions: { vector, btree_gist, pgcrypto } })

let pass = 0, fail = 0
const ok = (name) => { pass++; console.log(`  PASS  ${name}`) }
const ko = (name, e) => { fail++; console.log(`  FAIL  ${name}: ${e.message ?? e}`) }

async function expectError(name, sql, needle) {
  try {
    await db.exec(sql)
    ko(name, new Error('no lanzó error'))
  } catch (e) {
    if (!needle || String(e.message).includes(needle)) ok(name)
    else ko(name, e)
  }
}

// ── 1. Migraciones en orden + seed ──────────────────────────────────────────
const migDir = join(REPO, 'db/migrations')
for (const f of readdirSync(migDir).sort()) {
  const sql = readFileSync(join(migDir, f), 'utf8')
  try { await db.exec(sql); ok(`migración ${f}`) } catch (e) { ko(`migración ${f}`, e); process.exit(1) }
}
try { await db.exec(readFileSync(join(REPO, 'db/seed.sql'), 'utf8')); ok('seed Los Jazmines') }
catch (e) { ko('seed', e); process.exit(1) }

// ── 2. Seed counts ──────────────────────────────────────────────────────────
const LJ = '11111111-1111-1111-1111-111111111111'
const count = async (t) => (await db.query(`select count(*)::int c from ${t} where tenant_id = $1`, [LJ])).rows[0].c
if (await count('espacios') === 3) ok('seed: 3 espacios'); else ko('seed espacios', await count('espacios'))
if (await count('paquetes') === 4) ok('seed: 4 paquetes'); else ko('seed paquetes', await count('paquetes'))
if (await count('proveedores') === 10) ok('seed: 10 proveedores'); else ko('seed proveedores', await count('proveedores'))
if (await count('reglas_precio') >= 5) ok('seed: reglas de precio'); else ko('seed reglas', await count('reglas_precio'))

// ── 3. RLS ──────────────────────────────────────────────────────────────────
const A = 'aaaaaaaa-0000-0000-0000-000000000001'
const B = 'bbbbbbbb-0000-0000-0000-000000000002'
await db.exec(`
  insert into tenants (id, nombre) values ('${A}', 'tenant-A'), ('${B}', 'tenant-B');
  insert into leads (tenant_id, canal, nombre) values ('${A}', 'whatsapp', 'lead-A'), ('${B}', 'whatsapp', 'lead-B');
  insert into conocimiento (tenant_id, contenido, fuente) values ('${B}', 'secreto B', 'faq');
`)
await db.exec(`set role aiv_agent; select set_config('app.tenant_id', '${A}', false);`)

const leads = (await db.query('select nombre from leads')).rows.map(r => r.nombre)
if (leads.includes('lead-A') && !leads.includes('lead-B')) ok('RLS: select solo ve su tenant')
else ko('RLS select', new Error(JSON.stringify(leads)))

await expectError('RLS: insert cruzado bloqueado',
  `insert into leads (tenant_id, canal, nombre) values ('${B}', 'whatsapp', 'intruso')`,
  'row-level security')

const upd = await db.query(`update leads set nombre = 'hackeado' where nombre = 'lead-B'`)
if ((upd.affectedRows ?? 0) === 0) ok('RLS: update cruzado no afecta filas'); else ko('RLS update', upd.affectedRows)

const tenants = (await db.query('select id from tenants')).rows
if (tenants.length === 1 && tenants[0].id === A) ok('RLS: tenants solo se ve a sí mismo')
else ko('RLS tenants', new Error(JSON.stringify(tenants)))

const rag = (await db.query('select count(*)::int c from conocimiento')).rows[0].c
if (rag === 0) ok('RLS: fuga RAG entre tenants = negativo'); else ko('RLS conocimiento', rag)

await db.exec(`select set_config('app.tenant_id', '', false);`)
const sinCtx = (await db.query('select count(*)::int c from leads')).rows[0].c
if (sinCtx === 0) ok('RLS: sin contexto no ve nada'); else ko('RLS sin contexto', sinCtx)
await db.exec('reset role')

// ── 4. Anti doble-reserva (EXCLUDE USING gist) ─────────────────────────────
const esp = '11111111-1111-1111-1111-11111111e001'
const leadA = (await db.query(`select id from leads where nombre = 'lead-A'`)).rows[0].id
// lead-A pertenece al tenant A, pero la reserva es del tenant LJ (espacio LJ);
// para el constraint da igual: usamos lead del propio LJ.
const leadLJ = (await db.query(
  `insert into leads (tenant_id, canal, nombre) values ('${LJ}', 'whatsapp', 'lead-LJ') returning id`
)).rows[0].id
const reservar = (ini, fin, estado = 'confirmada') => db.query(
  `insert into reservas (tenant_id, lead_id, espacio_id, inicio, fin, estado)
   values ('${LJ}', '${leadLJ}', '${esp}', '${ini}', '${fin}', '${estado}') returning id`)

const r1 = await reservar('2026-09-12 15:00-05', '2026-09-12 20:00-05'); ok('reserva base creada')
await expectError('constraint: solape rechazado',
  `insert into reservas (tenant_id, lead_id, espacio_id, inicio, fin, estado)
   values ('${LJ}', '${leadLJ}', '${esp}', '2026-09-12 18:00-05', '2026-09-12 23:00-05', 'confirmada')`,
  'reservas_sin_solape')
try { await reservar('2026-09-12 20:00-05', '2026-09-12 23:00-05'); ok('constraint: rangos adyacentes permitidos') }
catch (e) { ko('adyacentes', e) }
const rHold = (await reservar('2026-09-13 15:00-05', '2026-09-13 20:00-05', 'hold')).rows[0].id
await expectError('constraint: hold también bloquea',
  `insert into reservas (tenant_id, lead_id, espacio_id, inicio, fin, estado)
   values ('${LJ}', '${leadLJ}', '${esp}', '2026-09-13 16:00-05', '2026-09-13 18:00-05', 'confirmada')`,
  'reservas_sin_solape')

// ── 5. Liberación auditada + cancelada no bloquea ───────────────────────────
await db.query(`update reservas set estado = 'cancelada' where id = $1`, [rHold])
const audit = (await db.query(
  `select accion from eventos_auditoria where entidad = 'reserva' and entidad_id = $1`, [rHold])).rows
if (audit.length === 1 && audit[0].accion.includes('hold -> cancelada')) ok('auditoría: liberación registrada')
else ko('auditoría', new Error(JSON.stringify(audit)))
try { await reservar('2026-09-13 16:00-05', '2026-09-13 19:00-05'); ok('constraint: cancelada no bloquea') }
catch (e) { ko('cancelada no bloquea', e) }

// ── 6. pgvector: columna e índice HNSW operativos + retriever aislado ───────
try {
  const v = JSON.stringify(Array.from({ length: 1024 }, (_, i) => (i % 7) / 7))
  await db.query(`insert into conocimiento (tenant_id, contenido, embedding, fuente)
                  values ($1, 'faq test', $2, 'test')`, [LJ, v])
  const near = await db.query(
    `select contenido from conocimiento where tenant_id = $1 order by embedding <=> $2 limit 1`, [LJ, v])
  if (near.rows[0].contenido === 'faq test') ok('pgvector: HNSW coseno operativo')
  else ko('pgvector', new Error('sin resultados'))

  // Fuga RAG entre tenants (query EXACTA de rag/retriever.py, como aiv_agent):
  // mismo vector para el doc del tenant B => si el aislamiento fallara,
  // saldría primero con score perfecto.
  await db.query(`insert into conocimiento (tenant_id, contenido, embedding, fuente)
                  values ($1, 'SECRETO-B', $2, 'faq')`, [B, v])
  await db.exec(`set role aiv_agent; select set_config('app.tenant_id', '${LJ}', false);`)
  const rec = await db.query(
    `select contenido, fuente, 1 - (embedding <=> $1::vector) as score
       from conocimiento
      where tenant_id = $2 and embedding is not null
      order by embedding <=> $1::vector
      limit 10`, [v, LJ])
  const contenidos = rec.rows.map(r => r.contenido).join(' ')
  if (rec.rows.length > 0 && !contenidos.includes('SECRETO-B'))
    ok('retriever: fuga RAG entre tenants = negativo')
  else ko('retriever fuga RAG', new Error(contenidos))
  await db.exec('reset role')
} catch (e) { ko('pgvector', e); await db.exec('reset role').catch(() => {}) }

// ── 7. metricas_tenant y webhook_eventos protegidas ─────────────────────────
await db.exec(`insert into webhook_eventos (fuente, evento_id) values ('whatsapp', 'wamid.1')`)
const dup = await db.query(
  `insert into webhook_eventos (fuente, evento_id) values ('whatsapp', 'wamid.1')
   on conflict (fuente, evento_id) do nothing returning id`)
if (dup.rows.length === 0) ok('dedup: reintento de webhook no se re-registra')
else ko('dedup', new Error('duplicado aceptado'))

await db.exec(`set role aiv_agent; select set_config('app.tenant_id', '${LJ}', false);`)
await expectError('metricas_tenant: sin acceso directo para aiv_agent',
  'select * from metricas_tenant', 'denied')
await expectError('webhook_eventos: sin acceso para aiv_agent',
  'select * from webhook_eventos', 'denied')
try {
  await db.query('select * from metricas_del_tenant()')
  ok('metricas_del_tenant(): accesible vía security definer')
} catch (e) { ko('metricas_del_tenant', e) }
await db.exec('reset role')

// ── 8. Dashboard: rol authenticated resuelve su tenant vía JWT (F7) ─────────
try {
  await db.exec(`
    select set_config('app.tenant_id', '', false);
    select set_config('request.jwt.claim.sub', '99999999-9999-9999-9999-999999999999', false);
    set role authenticated;`)

  const leadsAuth = (await db.query('select nombre, tenant_id from leads')).rows
  const soloLJ = leadsAuth.length > 0 && leadsAuth.every(r => r.tenant_id === LJ)
  if (soloLJ && !leadsAuth.some(r => ['lead-A', 'lead-B'].includes(r.nombre)))
    ok('dashboard: authenticated solo ve leads de su tenant (vía tenant_users)')
  else ko('dashboard leads', new Error(JSON.stringify(leadsAuth)))

  const tu = (await db.query('select tenant_id from tenant_users')).rows
  if (tu.length === 1 && tu[0].tenant_id === LJ) ok('dashboard: tenant_users self')
  else ko('dashboard tenant_users', new Error(JSON.stringify(tu)))

  await db.query('select * from metricas_del_tenant()')
  ok('dashboard: metricas_del_tenant() ejecutable por authenticated')

  await expectError('dashboard: authenticated no puede escribir leads',
    `insert into leads (tenant_id, canal) values ('${LJ}', 'whatsapp')`, 'denied')

  const apr = (await db.query('select count(*)::int c from aprobaciones')).rows[0].c
  if (apr === 0) ok('dashboard: bandeja de aprobaciones legible (RLS activa)')
  else ko('dashboard aprobaciones', apr)

  await db.exec('reset role')
} catch (e) { ko('dashboard authenticated', e); await db.exec('reset role').catch(() => {}) }

console.log(`\n${pass} PASS, ${fail} FAIL`)
await db.close()
process.exit(fail ? 1 : 0)
