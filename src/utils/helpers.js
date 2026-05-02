export const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
export const genId = (p = "id") => `${p}_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
export const fmt = (ts) => new Date(ts).toLocaleString();

/** e.g. "2 minutes ago" — use `fmt(ts)` in a `title` for full local datetime. */
export function formatRelativeAgo(ts) {
  const then = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (Number.isNaN(then)) return "";
  let diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 0) diffSec = 0;

  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

  if (diffSec < 45) return rtf.format(-diffSec, "second");
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return rtf.format(-diffMin, "minute");
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return rtf.format(-diffHr, "hour");
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return rtf.format(-diffDay, "day");
  const diffMonth = Math.floor(diffDay / 30);
  if (diffMonth < 12) return rtf.format(-diffMonth, "month");
  const diffYear = Math.floor(diffMonth / 12);
  return rtf.format(-diffYear, "year");
}

/** Local face-DB users are allowed access unless explicitly set to false in the map. */
export function isFaceAccessAllowed(userName, faceAccessMap) {
  if (!userName) return false;
  if (faceAccessMap && Object.prototype.hasOwnProperty.call(faceAccessMap, userName)) {
    return faceAccessMap[userName] !== false;
  }
  return true;
}
