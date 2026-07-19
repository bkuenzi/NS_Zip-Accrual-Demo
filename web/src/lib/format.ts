export function money(amount: string | number, currency?: string): string {
  const n = Number(amount);
  const s = n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return currency ? `${s} ${currency}` : s;
}

export function moneyCompact(amount: string | number): string {
  const n = Number(amount);
  if (Math.abs(n) >= 1_000_000)
    return `${(n / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 2 })}M`;
  if (Math.abs(n) >= 1_000)
    return `${(n / 1_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}k`;
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 16).replace("T", " ");
}
