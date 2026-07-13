import { useEffect, useMemo, useState } from "react";
import { supabase } from "../lib/supabase";
import type { Reserva } from "../lib/tipos";

const COLOR: Record<string, string> = {
  hold: "bg-amber-400",
  confirmada: "bg-emerald-500",
  ejecutada: "bg-neutral-400",
};

export default function Calendario() {
  const [mes, setMes] = useState(() => {
    const hoy = new Date();
    return new Date(hoy.getFullYear(), hoy.getMonth(), 1);
  });
  const [reservas, setReservas] = useState<Reserva[]>([]);

  useEffect(() => {
    const desde = mes.toISOString();
    const hasta = new Date(mes.getFullYear(), mes.getMonth() + 1, 1).toISOString();
    supabase
      .from("reservas")
      .select("id, inicio, fin, estado, espacios(nombre), leads(nombre, telefono)")
      .gte("inicio", desde)
      .lt("inicio", hasta)
      .in("estado", ["hold", "confirmada", "ejecutada"])
      .order("inicio")
      .then(({ data }) => setReservas((data as unknown as Reserva[]) ?? []));
  }, [mes]);

  const porDia = useMemo(() => {
    const mapa = new Map<number, Reserva[]>();
    for (const r of reservas) {
      const d = new Date(r.inicio).getDate();
      mapa.set(d, [...(mapa.get(d) ?? []), r]);
    }
    return mapa;
  }, [reservas]);

  const primerDow = (mes.getDay() + 6) % 7; // lunes = 0
  const dias = new Date(mes.getFullYear(), mes.getMonth() + 1, 0).getDate();

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xl font-bold text-neutral-800">Agenda</h2>
        <div className="flex items-center gap-2 text-sm">
          <button onClick={() => setMes(new Date(mes.getFullYear(), mes.getMonth() - 1, 1))}
                  className="rounded-lg bg-white px-2 py-1 shadow-sm">◀</button>
          <span className="w-32 text-center font-medium capitalize">
            {mes.toLocaleDateString("es-PE", { month: "long", year: "numeric" })}
          </span>
          <button onClick={() => setMes(new Date(mes.getFullYear(), mes.getMonth() + 1, 1))}
                  className="rounded-lg bg-white px-2 py-1 shadow-sm">▶</button>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-7 gap-1 rounded-xl bg-white p-2 shadow-sm">
        {["L", "M", "X", "J", "V", "S", "D"].map((d) => (
          <div key={d} className="py-1 text-center text-xs font-medium text-neutral-400">{d}</div>
        ))}
        {Array.from({ length: primerDow }).map((_, i) => <div key={`v${i}`} />)}
        {Array.from({ length: dias }, (_, i) => i + 1).map((dia) => (
          <div key={dia} className="flex aspect-square flex-col items-center justify-center rounded-lg text-sm">
            <span className="text-neutral-700">{dia}</span>
            <div className="flex h-1.5 gap-0.5">
              {(porDia.get(dia) ?? []).slice(0, 3).map((r) => (
                <span key={r.id} className={`h-1.5 w-1.5 rounded-full ${COLOR[r.estado]}`} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <ul className="space-y-2">
        {reservas.map((r) => (
          <li key={r.id} className="flex items-center gap-3 rounded-xl bg-white p-3 shadow-sm">
            <span className={`h-3 w-3 shrink-0 rounded-full ${COLOR[r.estado]}`} />
            <div className="min-w-0">
              <p className="font-medium text-neutral-800">
                {new Date(r.inicio).toLocaleDateString("es-PE", { day: "numeric", month: "short" })}
                {" · "}{r.espacios?.nombre ?? "Espacio"}
              </p>
              <p className="truncate text-xs text-neutral-500">
                {r.leads?.nombre ?? r.leads?.telefono ?? "Cliente"} · {r.estado}
              </p>
            </div>
          </li>
        ))}
        {reservas.length === 0 && (
          <p className="py-8 text-center text-sm text-neutral-400">Sin reservas este mes.</p>
        )}
      </ul>
    </section>
  );
}
