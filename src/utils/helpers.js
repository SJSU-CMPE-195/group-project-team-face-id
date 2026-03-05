export const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
export const genId = (p = "id") => `${p}_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
export const fmt = (ts) => new Date(ts).toLocaleString();
