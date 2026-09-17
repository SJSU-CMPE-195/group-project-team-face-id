import { useMemo } from "react";

async function fetchJson(url, opts = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;
  if (!response.ok) {
    const detail = data?.error || data?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `HTTP ${response.status} ${response.statusText}`,
    );
  }
  if (!contentType.includes("application/json")) {
    throw new Error(`Expected JSON response from ${url}`);
  }
  return data;
}

async function fetchFormJson(url, form) {
  const response = await fetch(url, {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;
  if (!response.ok) {
    const detail = data?.error || data?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `HTTP ${response.status} ${response.statusText}`,
    );
  }
  if (!contentType.includes("application/json")) {
    throw new Error(`Expected JSON response from ${url}`);
  }
  return data;
}

export default function useApi(baseUrl) {
  return useMemo(() => {
    const clean = (baseUrl || "").trim().replace(/\/$/, "");
    const endpoint = (path) => {
      const isHttpUrl = /^https?:\/\//i.test(clean);
      const isRootRelativeUrl = clean.startsWith("/") && !clean.startsWith("//");
      if (!isHttpUrl && !isRootRelativeUrl) {
        throw new Error(
          "Backend URL must use HTTP, HTTPS, or a root-relative proxy path.",
        );
      }
      return `${clean}${path}`;
    };

    return {
      status: () => fetchJson(endpoint("/api/status")),
      cameraStatus: (signal) =>
        fetchJson(endpoint("/api/camera/status"), { signal }),
      cameraStreamUrl: (sessionId) =>
        endpoint(
          `/api/camera/stream?session_id=${encodeURIComponent(sessionId)}`,
        ),
      lock: () =>
        fetchJson(endpoint("/api/lock"), {
          method: "POST",
          body: JSON.stringify({ reason: "manual_ui" }),
        }),
      ignitionStop: () =>
        fetchJson(endpoint("/api/ignition/stop"), { method: "POST" }),
      fullReset: () =>
        fetchJson(endpoint("/api/full-reset"), { method: "POST" }),
      users: () => fetchJson(endpoint("/api/users")),
      faceStatus: () => fetchJson(endpoint("/api/face-status")),
      addUser: (name) =>
        fetchJson(endpoint("/api/users"), {
          method: "POST",
          body: JSON.stringify({ name }),
        }),
      delUser: (id) =>
        fetchJson(endpoint(`/api/users/${encodeURIComponent(id)}`), {
          method: "DELETE",
        }),
      setAccess: (id, allowed) =>
        fetchJson(
          endpoint(`/api/users/${encodeURIComponent(id)}/access`),
          {
            method: "PATCH",
            body: JSON.stringify({ allowed }),
          },
        ),
      logs: () => fetchJson(endpoint("/api/logs")),
      verifyLog: (result, detail, userId) =>
        fetchJson(endpoint("/api/verify-log"), {
          method: "POST",
          body: JSON.stringify({ result, detail, user_id: userId }),
        }),
      getSettings: () => fetchJson(endpoint("/api/settings")),
      saveSettings: (settings) =>
        fetchJson(endpoint("/api/settings"), {
          method: "POST",
          body: JSON.stringify(settings),
        }),
      scanStart: (payload = {}) =>
        fetchJson(endpoint("/api/scan/start"), {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      scanStatus: (sessionId) =>
        fetchJson(
          endpoint(
            `/api/scan/status?session_id=${encodeURIComponent(sessionId)}`,
          ),
        ),
      scanCancel: (sessionId) =>
        fetchJson(endpoint("/api/scan/cancel"), {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        }),
      piEnrollStart: (payload = {}) =>
        fetchJson(endpoint("/api/enroll/start"), {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      piEnrollStatus: (sessionId) =>
        fetchJson(
          endpoint(
            `/api/enroll/status?session_id=${encodeURIComponent(sessionId)}`,
          ),
        ),
      piEnrollCancel: (sessionId) =>
        fetchJson(endpoint("/api/enroll/cancel"), {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        }),
      piEnrollSample: (sessionId, blob) => {
        const form = new FormData();
        form.append("session_id", sessionId);
        form.append("image", blob, "sample.jpg");
        return fetchFormJson(endpoint("/api/enroll/sample"), form);
      },
      piEnrollFinish: (sessionId) =>
        fetchJson(endpoint("/api/enroll/finish"), {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        }),
    };
  }, [baseUrl]);
}
