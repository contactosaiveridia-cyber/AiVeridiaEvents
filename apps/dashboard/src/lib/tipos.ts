export interface Lead {
  id: string;
  nombre: string | null;
  telefono: string | null;
  canal: string;
  estado: string;
  creado_en: string;
}

export interface Reserva {
  id: string;
  inicio: string;
  fin: string;
  estado: string;
  espacios: { nombre: string } | null;
  leads: { nombre: string | null; telefono: string | null } | null;
}

export interface Aprobacion {
  id: string;
  tipo: "aprobacion_descuento" | "proveedor_sin_confirmar";
  referencia: string;
  payload: Record<string, unknown>;
  creado_en: string;
}

export interface MetricaMes {
  tenant_id: string;
  mes: string;
  leads: number;
  convertidos: number;
  tasa_conversion_pct: number | null;
  ingresos_generados: number;
}

export const ESTADOS_EMBUDO = [
  "nuevo", "calificado", "cotizado", "seguimiento",
  "convertido", "nurturing", "perdido",
] as const;

export const COLOR_ESTADO: Record<string, string> = {
  nuevo: "bg-sky-100 text-sky-800",
  calificado: "bg-indigo-100 text-indigo-800",
  cotizado: "bg-violet-100 text-violet-800",
  seguimiento: "bg-amber-100 text-amber-800",
  convertido: "bg-emerald-100 text-emerald-800",
  nurturing: "bg-neutral-200 text-neutral-700",
  perdido: "bg-rose-100 text-rose-800",
};
