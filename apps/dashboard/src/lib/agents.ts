/** Cliente de la API de agents: solo la ACCIÓN de responder aprobaciones
 * (las lecturas van directo a Supabase con RLS). */

const BASE = import.meta.env.VITE_AGENTS_URL as string;
const TOKEN = import.meta.env.VITE_AGENTS_TOKEN as string;

export async function responderAprobacion(datos: {
  tenant_id: string;
  tipo: string;
  referencia: string;
  aprobada: boolean;
  proveedor_sustituto_id?: string;
}): Promise<void> {
  const r = await fetch(`${BASE}/owner/aprobaciones/responder`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Internal-Token": TOKEN },
    body: JSON.stringify(datos),
  });
  if (!r.ok) throw new Error(`Error ${r.status}: ${await r.text()}`);
}
