export const fmtUsd = (n) => {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n) || 0;
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1_000_000) return `${sign}$${(a / 1_000_000).toFixed(2)}M`;
  if (a >= 1_000) return `${sign}$${(a / 1_000).toFixed(1)}K`;
  return `${sign}$${a.toFixed(0)}`;
};

export const fmtNum = (n) => {
  const a = Math.abs(Number(n) || 0);
  if (a >= 1_000_000) return `${(a / 1_000_000).toFixed(1)}M`;
  if (a >= 1_000) return `${(a / 1_000).toFixed(1)}K`;
  return `${a}`;
};

export const fmtCents = (p) => `${Math.round((Number(p) || 0) * 100)}¢`;

export const fmtPct = (x, digits = 0) => x == null || !Number.isFinite(Number(x)) ? "—" : `${(Number(x) * 100).toFixed(digits)}%`;

export const fmtSignedPct = (x, digits = 0) => {
  if (x == null || !Number.isFinite(Number(x))) return "—";
  const v = (Number(x) || 0) * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
};

export const shortAddr = (a = "") =>
  a && a.length > 10 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;

export const scoreColor = (s) => {
  const v = Number(s) || 0;
  if (v >= 72) return "#00E599";
  if (v >= 58) return "#00D2FF";
  if (v >= 44) return "#FFB020";
  return "#FF3B5C";
};

export const timeAgo = (iso) => {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
};

export const countdown = (iso) => {
  if (!iso) return null;
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return "RESOLVING";
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);
  if (d > 0) return `${d}d ${h}h`;
  const m = Math.floor((diff % 3600000) / 60000);
  return `${h}h ${m}m`;
};
