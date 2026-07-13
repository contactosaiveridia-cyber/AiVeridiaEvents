import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { COLOR_ESTADO, ESTADOS_EMBUDO, type Lead } from "../lib/tipos";

export default function Embudo() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [filtro, setFiltro] = useState<string>("todos");

  useEffect(() => {
    supabase
      .from("leads")
      .select("id, nombre, telefono, canal, estado, creado_en")
      .order("creado_en", { ascending: false })
      .limit(200)
      .then(({ data }) => setLeads((data as Lead[]) ?? []));
  }, []);

  const conteos = Object.fromEntries(
    ESTADOS_EMBUDO.map((e) => [e, leads.filter((l) => l.estado === e).length]),
  );
  const visibles = filtro === "todos" ? leads : leads.filter((l) => l.estado === filtro);

  return (
    <section>
      <h2 className="mb-3 text-xl font-bold text-neutral-800">Embudo de leads</h2>

      <div className="-mx-4 mb-4 flex gap-2 overflow-x-auto px-4 pb-1">
        <Chip activo={filtro === "todos"} onClick={() => setFiltro("todos")}>
          Todos ({leads.length})
        </Chip>
        {ESTADOS_EMBUDO.map((e) => (
          <Chip key={e} activo={filtro === e} onClick={() => setFiltro(e)}>
            {e} ({conteos[e]})
          </Chip>
        ))}
      </div>

      <ul className="space-y-2">
        {visibles.map((l) => (
          <li key={l.id} className="rounded-xl bg-white p-3 shadow-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium text-neutral-800">
                {l.nombre ?? l.telefono ?? "Lead sin nombre"}
              </span>
              <span className={`rounded-full px-2 py-0.5 text-xs ${COLOR_ESTADO[l.estado] ?? ""}`}>
                {l.estado}
              </span>
            </div>
            <p className="mt-1 text-xs text-neutral-500">
              {l.canal} · {new Date(l.creado_en).toLocaleDateString("es-PE")}
              {l.telefono ? ` · ${l.telefono}` : ""}
            </p>
          </li>
        ))}
        {visibles.length === 0 && (
          <p className="py-8 text-center text-sm text-neutral-400">Sin leads aún.</p>
        )}
      </ul>
    </section>
  );
}

function Chip({ activo, onClick, children }: {
  activo: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`whitespace-nowrap rounded-full px-3 py-1.5 text-sm ${
        activo ? "bg-violet-700 text-white" : "bg-white text-neutral-600 shadow-sm"
      }`}
    >
      {children}
    </button>
  );
}
