export const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
export const genId = (p = "id") => `${p}_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
export const fmt = (ts) => new Date(ts).toLocaleString();

/** e.g. "2 minutes ago" — use `fmt(ts)` in a `title` for full local datetime. */
export function formatRelativeAgo(ts) {
  const then = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (Number.isNaN(then)) return "";
  let diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 0) diffSec = 0;
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? "" : "s"} ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`;
}

/** Local face-DB users are allowed access unless explicitly set to false in the map. */
export function isFaceAccessAllowed(userName, faceAccessMap) {
  if (!userName) return false;
  if (faceAccessMap && Object.prototype.hasOwnProperty.call(faceAccessMap, userName)) {
    return faceAccessMap[userName] !== false;
  }
  return true;
}
