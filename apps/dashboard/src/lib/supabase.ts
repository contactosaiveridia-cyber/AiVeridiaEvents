import { createClient } from "@supabase/supabase-js";

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL as string,
  import.meta.env.VITE_SUPABASE_ANON_KEY as string,
);

/** El tenant del dueño logueado (tenant_users, RLS self). */
export async function tenantActual(): Promise<string | null> {
  const { data } = await supabase.from("tenant_users").select("tenant_id").limit(1);
  return data?.[0]?.tenant_id ?? null;
}
