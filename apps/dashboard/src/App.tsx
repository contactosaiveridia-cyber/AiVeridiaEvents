import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import type { Session } from "@supabase/supabase-js";
import { supabase, tenantActual } from "./lib/supabase";
import Login from "./paginas/Login";
import Embudo from "./paginas/Embudo";
import Calendario from "./paginas/Calendario";
import Aprobaciones from "./paginas/Aprobaciones";
import Metricas from "./paginas/Metricas";

const TABS = [
  { a: "/embudo", icono: "👥", nombre: "Embudo" },
  { a: "/calendario", icono: "📅", nombre: "Agenda" },
  { a: "/aprobaciones", icono: "✋", nombre: "Aprobar" },
  { a: "/metricas", icono: "📈", nombre: "Métricas" },
];

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [tenant, setTenant] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setCargando(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (session) tenantActual().then(setTenant);
    else setTenant(null);
  }, [session]);

  if (cargando) return <Pantalla>Cargando…</Pantalla>;
  if (!session) return <Login />;
  if (!tenant) return <Pantalla>Tu usuario no está vinculado a ningún salón.</Pantalla>;

  return (
    <div className="mx-auto flex min-h-dvh max-w-lg flex-col">
      <header className="sticky top-0 z-10 flex items-center justify-between bg-violet-700 px-4 py-3 text-white shadow">
        <h1 className="text-lg font-semibold">Mi salón</h1>
        <button className="text-sm opacity-80" onClick={() => supabase.auth.signOut()}>
          Salir
        </button>
      </header>

      <main className="flex-1 px-4 pb-24 pt-4">
        <Routes>
          <Route path="/embudo" element={<Embudo />} />
          <Route path="/calendario" element={<Calendario />} />
          <Route path="/aprobaciones" element={<Aprobaciones tenant={tenant} />} />
          <Route path="/metricas" element={<Metricas />} />
          <Route path="*" element={<Navigate to="/embudo" replace />} />
        </Routes>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-10 mx-auto flex max-w-lg justify-around border-t border-neutral-200 bg-white pb-[env(safe-area-inset-bottom)]">
        {TABS.map((t) => (
          <NavLink
            key={t.a}
            to={t.a}
            className={({ isActive }) =>
              `flex flex-col items-center px-3 py-2 text-xs ${
                isActive ? "font-semibold text-violet-700" : "text-neutral-500"
              }`
            }
          >
            <span className="text-xl">{t.icono}</span>
            {t.nombre}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function Pantalla({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center p-6 text-neutral-600">
      {children}
    </div>
  );
}
