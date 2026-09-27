import { useMemo } from "react";
import useSecurity from "../security/useSecurity.js";

async function fetchJson(url, options = {}, onUnauthorized) {
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;
  if (response.status === 401) onUnauthorized?.();
  if (!response.ok) {
    const detail = data?.message || data?.error || data?.detail;
    const error = new Error(
      typeof detail === "string"
        ? detail
        : `HTTP ${response.status} ${response.statusText}`,
    );
    error.status = response.status;
    error.code = data?.code;
    error.payload = data;
    throw error;
  }
  if (!contentType.includes("application/json")) {
    throw new Error(`Expected JSON response from ${url}`);
  }
  return data;
}

async function fetchFormJson(url, form, headers, onUnauthorized) {
  const response = await fetch(url, {
    method: "POST",
    body: form,
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
    headers,
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;
  if (response.status === 401) onUnauthorized?.();
  if (!response.ok) {
    const detail = data?.message || data?.error || data?.detail;
    const error = new Error(
      typeof detail === "string"
        ? detail
        : `HTTP ${response.status} ${response.statusText}`,
    );
    error.status = response.status;
    error.code = data?.code;
    error.payload = data;
    throw error;
  }
  if (!contentType.includes("application/json")) {
    throw new Error(`Expected JSON response from ${url}`);
  }
  return data;
}

export default function useApi(baseUrl) {
  const { csrfToken, expireSession } = useSecurity();

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
    const get = (path, options) =>
      fetchJson(endpoint(path), options, expireSession);
    const mutate = (path, options = {}, grantToken = "") =>
      get(path, {
        ...options,
        headers: {
          "X-BASS-CSRF": csrfToken,
          ...(grantToken
            ? { "X-BASS-Operation-Grant": grantToken }
            : {}),
          ...(options.headers || {}),
        },
      });

    return {
      status: () => get("/api/status"),
      deviceInfo: () => get("/api/device-info"),
      cameraStatus: (signal) => get("/api/camera/status", { signal }),
      cameraStreamUrl: (sessionId) =>
        endpoint(
          `/api/camera/stream?session_id=${encodeURIComponent(sessionId)}`,
        ),
      lock: (grantToken) =>
        mutate(
          "/api/lock",
          {
            method: "POST",
            body: JSON.stringify({ reason: "manual_ui" }),
          },
          grantToken,
        ),
      ignitionStop: (grantToken) =>
        mutate("/api/ignition/stop", { method: "POST" }, grantToken),
      fullReset: (grantToken) =>
        mutate("/api/full-reset", { method: "POST" }, grantToken),
      users: () => get("/api/users"),
      faceStatus: () => get("/api/face-status"),
      addUser: (name, pin, isAdmin, grantToken, enrollFace = false) =>
        mutate(
          "/api/users",
          {
            method: "POST",
            body: JSON.stringify({
              name,
              pin,
              is_admin: !!isAdmin,
              enroll_face: enrollFace,
            }),
          },
          grantToken,
        ),
      delUser: (id, grantToken) =>
        mutate(
          `/api/users/${encodeURIComponent(id)}`,
          { method: "DELETE" },
          grantToken,
        ),
      setAccess: (id, allowed, grantToken) =>
        mutate(
          `/api/users/${encodeURIComponent(id)}/access`,
          { method: "PATCH", body: JSON.stringify({ allowed }) },
          grantToken,
        ),
      resetUserPin: (id, pin, grantToken) =>
        mutate(
          `/api/users/${encodeURIComponent(id)}/pin`,
          { method: "POST", body: JSON.stringify({ pin }) },
          grantToken,
        ),
      pairingInvite: (userId, grantToken) =>
        mutate(
          "/api/pairing-invites",
          { method: "POST", body: JSON.stringify({ user_id: userId }) },
          grantToken,
        ),
      devices: () => get("/api/devices"),
      revokeDevice: (deviceId, grantToken) =>
        mutate(
          `/api/devices/${encodeURIComponent(deviceId)}/revoke`,
          { method: "POST" },
          grantToken,
        ),
      logs: () => get("/api/logs"),
      getSettings: () => get("/api/settings"),
      saveSettings: (settings, grantToken) =>
        mutate(
          "/api/settings",
          { method: "POST", body: JSON.stringify(settings) },
          grantToken,
        ),
      scanStart: (payload = {}, grantToken) =>
        mutate(
          "/api/scan/start",
          { method: "POST", body: JSON.stringify(payload) },
          grantToken,
        ),
      scanStatus: (sessionId) =>
        get(`/api/scan/status?session_id=${encodeURIComponent(sessionId)}`),
      scanCancel: (sessionId) =>
        mutate("/api/scan/cancel", {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        }),
      piEnrollStart: (payload = {}, grantToken) =>
        mutate(
          "/api/enroll/start",
          { method: "POST", body: JSON.stringify(payload) },
          grantToken,
        ),
      piEnrollStatus: (sessionId) =>
        get(`/api/enroll/status?session_id=${encodeURIComponent(sessionId)}`),
      piEnrollCancel: (sessionId) =>
        mutate("/api/enroll/cancel", {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        }),
      piEnrollSample: (sessionId, blob) => {
        const form = new FormData();
        form.append("session_id", sessionId);
        form.append("image", blob, "sample.jpg");
        return fetchFormJson(
          endpoint("/api/enroll/sample"),
          form,
          { "X-BASS-CSRF": csrfToken },
          expireSession,
        );
      },
      piEnrollFinish: (sessionId) =>
        mutate("/api/enroll/finish", {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        }),
    };
  }, [baseUrl, csrfToken, expireSession]);
}
