export const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
export const genId = (p = "id") => `${p}_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
export const fmt = (ts) => new Date(ts).toLocaleString();

/** Local face-DB users are allowed access unless explicitly set to false in the map. */
export function isFaceAccessAllowed(userName, faceAccessMap) {
  if (!userName) return false;
  if (faceAccessMap && Object.prototype.hasOwnProperty.call(faceAccessMap, userName)) {
    return faceAccessMap[userName] !== false;
  }
  return true;
}
