import { useCallback, useEffect, useState } from "react";
import { responderAprobacion } from "../lib/agents";
import { supabase } from "../lib/supabase";
import type { Aprobacion } from "../lib/tipos";

export default function Aprobaciones({ tenant }: { tenant: string }) {
  const [pendientes, setPendientes] = useState<Aprobacion[]>([]);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const cargar = useCallback(() => {
    supabase
      .from("aprobaciones")
      .select("id, tipo, referencia, payload, creado_en")
      .eq("estado", "pendiente")
      .order("creado_en")
      .then(({ data }) => setPendientes((data as Aprobacion[]) ?? []));
  }, []);

  useEffect(cargar, [cargar]);

  async function decidir(a: Aprobacion, aprobada: boolean) {
    setOcupado(a.id);
    setError(null);
    try {
      await responderAprobacion({
        tenant_id: tenant, tipo: a.tipo, referencia: a.referencia, aprobada,
      });
      cargar();
    } catch {
      setError("No se pudo enviar la decisión. Intenta de nuevo.");
    } finally {
      setOcupado(null);
    }
  }

  return (
    <section>
      <h2 className="mb-3 text-xl font-bold text-neutral-800">Aprobaciones pendientes</h2>
      {error && <p className="mb-3 text-sm text-rose-600">{error}</p>}

      <ul className="space-y-3">
        {pendientes.map((a) => (
          <li key={a.id} className="rounded-xl bg-white p-4 shadow-sm">
            {a.tipo === "aprobacion_descuento" ? (
              <>
                <p className="font-medium text-neutral-800">
                  💸 Descuento de {String(a.payload.descuento_pct)}%
                </p>
                <p className="mt-1 text-sm text-neutral-500">
                  Precio final: S/ {Number(a.payload.precio_final ?? 0).toFixed(2)}
                </p>
              </>
            ) : (
              <>
                <p className="font-medium text-neutral-800">🚚 Proveedor sin confirmar</p>
                <p className="mt-1 text-sm text-neutral-500">
                  Evento del {String(a.payload.fecha_evento ?? "—")} — gestionar reemplazo
                </p>
              </>
            )}
            <p className="mt-1 text-xs text-neutral-400">
              {new Date(a.creado_en).toLocaleString("es-PE")}
            </p>
            <div className="mt-3 flex gap-2">
              <button
                disabled={ocupado === a.id}
                onClick={() => decidir(a, true)}
                className="flex-1 rounded-xl bg-emerald-600 py-2.5 font-semibold text-white active:bg-emerald-700 disabled:opacity-50"
              >
                Aprobar
              </button>
              <button
                disabled={ocupado === a.id}
                onClick={() => decidir(a, false)}
                className="flex-1 rounded-xl bg-rose-100 py-2.5 font-semibold text-rose-700 active:bg-rose-200 disabled:opacity-50"
              >
                Rechazar
              </button>
            </div>
          </li>
        ))}
        {pendientes.length === 0 && (
          <p className="py-8 text-center text-sm text-neutral-400">
            Nada pendiente. El agente sigue trabajando solo 🤖
          </p>
        )}
      </ul>
    </section>
  );
}
