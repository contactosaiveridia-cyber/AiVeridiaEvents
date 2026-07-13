import { useState } from "react";
import { supabase } from "../lib/supabase";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setError("Correo o contraseña incorrectos.");
    setEnviando(false);
  }

  return (
    <div className="flex min-h-dvh flex-col justify-center bg-violet-700 p-6">
      <div className="mx-auto w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl">
        <h1 className="text-center text-2xl font-bold text-violet-700">
          aiVeridia Events
        </h1>
        <p className="mb-6 text-center text-sm text-neutral-500">
          El panel de tu salón, en tu bolsillo
        </p>
        <form onSubmit={entrar} className="space-y-3">
          <input
            type="email"
            required
            placeholder="Correo"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-xl border border-neutral-300 px-4 py-3 text-base outline-violet-500"
          />
          <input
            type="password"
            required
            placeholder="Contraseña"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl border border-neutral-300 px-4 py-3 text-base outline-violet-500"
          />
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <button
            disabled={enviando}
            className="w-full rounded-xl bg-violet-700 py-3 font-semibold text-white active:bg-violet-800 disabled:opacity-60"
          >
            {enviando ? "Entrando…" : "Entrar"}
          </button>
        </form>
      </div>
    </div>
  );
}
