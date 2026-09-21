export const FINAL_SCAN_STATES = new Set([
  "granted",
  "denied",
  "error",
  "cancelled",
  "timeout",
  "completed",
]);

export function normalizeScan(raw = {}, fallbackPurpose = "unlock") {
  const state =
    raw.state ||
    raw.result ||
    (raw.granted === true
      ? "granted"
      : raw.granted === false
        ? "denied"
        : "scanning");

  return {
    ok: raw.ok !== false,
    sessionId: raw.session_id || raw.sessionId || null,
    state,
    purpose: raw.purpose || fallbackPurpose,
    source: raw.camera_source || raw.source || null,
    user: raw.user || raw.candidate_user || raw.candidateUser || null,
    score: raw.score ?? raw.best_score ?? null,
    faceCount: raw.face_count ?? raw.faceCount ?? null,
    message: raw.message || raw.detail || raw.error || "",
    kind: raw.kind || "scan",
    count: raw.count ?? null,
    samplesNeeded: raw.samples_needed ?? raw.samplesNeeded ?? null,
    updatedAt: raw.updated_at ?? raw.updatedAt ?? Date.now(),
    window: raw.window || {
      matches: raw.matches ?? raw.candidate_count ?? 0,
      needed: raw.needed ?? raw.min_matches ?? 6,
      size: raw.size ?? raw.window_size ?? 10,
    },
  };
}

export function cameraLabel(source, fallbackSource) {
  const resolvedSource =
    source === "pc_webcam" || source === "pi_camera" ? source : fallbackSource;
  if (resolvedSource === "pc_webcam") return "PC webcam";
  if (resolvedSource === "pi_camera") return "Pi camera";
  return "Backend camera";
}

export function sessionTimestamp(value) {
  const numericValue = Number(value);
  if (Number.isFinite(numericValue) && numericValue > 0) return numericValue;
  const parsedValue = Date.parse(value || "");
  return Number.isFinite(parsedValue) ? parsedValue : null;
}

export function scanBadge(state) {
  if (state === "granted" || state === "completed") return "ok";
  if (state === "denied" || state === "error" || state === "timeout") {
    return "err";
  }
  if (state === "cancelled") return "warn";
  return "info";
}

export function scanLabel(state) {
  if (state === "granted") return "Granted";
  if (state === "denied") return "Denied";
  if (state === "error") return "Error";
  if (state === "timeout") return "Timeout";
  if (state === "cancelled") return "Cancelled";
  if (state === "completed") return "Completed";
  if (state === "cancelling") return "Cancelling";
  if (state === "starting") return "Starting";
  return "Scanning";
}

export function confirmedCancellation(raw, purpose) {
  const cancelledScan = normalizeScan(raw, purpose);
  if (!cancelledScan.ok || cancelledScan.state !== "cancelled") {
    throw new Error(
      cancelledScan.message || "Backend did not confirm cancellation.",
    );
  }
  return cancelledScan;
}
