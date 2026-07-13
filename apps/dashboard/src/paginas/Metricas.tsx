import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import type { MetricaMes } from "../lib/tipos";

export default function Metricas() {
  const [filas, setFilas] = useState<MetricaMes[]>([]);

  useEffect(() => {
    supabase.rpc("metricas_del_tenant").then(({ data }) => {
      const ordenadas = ((data as MetricaMes[]) ?? []).sort(
        (a, b) => b.mes.localeCompare(a.mes),
      );
      setFilas(ordenadas);
    });
  }, []);

  const actual = filas[0];
  const totalLeads = filas.reduce((s, f) => s + f.leads, 0);
  const totalConv = filas.reduce((s, f) => s + f.convertidos, 0);
  const totalIngresos = filas.reduce((s, f) => s + Number(f.ingresos_generados), 0);
  const maxLeads = Math.max(1, ...filas.map((f) => f.leads));

  return (
    <section>
      <h2 className="mb-3 text-xl font-bold text-neutral-800">Métricas</h2>

      <div className="mb-4 grid grid-cols-3 gap-2">
        <Tarjeta titulo="Leads" valor={String(totalLeads)} />
        <Tarjeta titulo="Reservas" valor={String(totalConv)} />
        <Tarjeta titulo="Ingresos" valor={`S/ ${totalIngresos.toLocaleString("es-PE")}`} />
      </div>

      {actual && (
        <div className="mb-4 rounded-xl bg-violet-700 p-4 text-white shadow">
          <p className="text-sm opacity-80">
            Conversión de {new Date(actual.mes).toLocaleDateString("es-PE", { month: "long" })}
          </p>
          <p className="text-3xl font-bold">{actual.tasa_conversion_pct ?? 0}%</p>
        </div>
      )}

      <div className="rounded-xl bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-neutral-600">Leads por mes</h3>
        <ul className="space-y-2">
          {filas.map((f) => (
            <li key={f.mes} className="flex items-center gap-2 text-sm">
              <span className="w-16 shrink-0 capitalize text-neutral-500">
                {new Date(f.mes).toLocaleDateString("es-PE", { month: "short", year: "2-digit" })}
              </span>
              <div className="h-4 rounded bg-violet-500"
                   style={{ width: `${(f.leads / maxLeads) * 100}%` }} />
              <span className="text-neutral-600">{f.leads}</span>
            </li>
          ))}
          {filas.length === 0 && (
            <p className="py-4 text-center text-sm text-neutral-400">
              Aún no hay datos suficientes.
            </p>
          )}
        </ul>
      </div>
    </section>
  );
}

function Tarjeta({ titulo, valor }: { titulo: string; valor: string }) {
  return (
    <div className="rounded-xl bg-white p-3 text-center shadow-sm">
      <p className="text-xs text-neutral-500">{titulo}</p>
      <p className="mt-1 truncate text-lg font-bold text-neutral-800">{valor}</p>
    </div>
  );
}
