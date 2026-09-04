import { useLayoutEffect, useMemo, useRef } from "react";
import { genId } from "../utils/helpers";

// The Device API identifies callers by an HttpOnly session cookie, so there is
// no credential for this module to hold or attach -- the browser sends it.
// Every request opts into credentials, and nothing here can leak a secret
// because nothing here has one.
export class AuthError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

function describeError(res, data) {
  const detail = data?.error || data?.detail;
  if (res.status === 401) {
    return "Your session has ended. Pair this device again to continue.";
  }
  if (res.status === 403) {
    return typeof detail === "string" && detail
      ? detail
      : "Your account does not have permission for this action.";
  }
  if (res.status === 429) {
    return "Too many attempts. Wait a moment and try again.";
  }
  if (typeof detail === "string") return detail;
  return `HTTP ${res.status} ${res.statusText}`;
}

// 401 and 403 are authorization outcomes, not connectivity failures. Callers
// must tell them apart so a permission error does not read as "offline".
function raise(res, data) {
  const message = describeError(res, data);
  if (res.status === 401 || res.status === 403) {
    throw new AuthError(message, res.status);
  }
  throw new Error(message);
}

async function fetchJson(url, opts = {}) {
  const res = await fetch(url, {
    cache: "no-store",
    credentials: "include",
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    raise(res, data);
  }
  if (!ct.includes("application/json")) {
    throw new Error(`Expected JSON response from ${url}`);
  }
  return data;
}

async function fetchFormJson(url, form) {
  const res = await fetch(url, {
    method: "POST",
    body: form,
    cache: "no-store",
    credentials: "include",
  });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    raise(res, data);
  }
  if (!ct.includes("application/json")) throw new Error(`Expected JSON response from ${url}`);
  return data;
}

function isMockPiUrl(url) {
  return (url || "").trim().replace(/\/$/, "").toLocaleLowerCase() === "fake://pi";
}

export default function useApi(mode, baseUrl, sim, setSim) {
  const simRef = useRef(sim);

  useLayoutEffect(() => {
    simRef.current = sim;
  }, [sim]);

  return useMemo(() => {
    const updateSim = (updater) => {
      setSim((current) => {
        const next = updater(current);
        simRef.current = next;
        return next;
      });
    };

    if (mode === "device") {
      const clean = (baseUrl || "").trim().replace(/\/$/, "");
      if (isMockPiUrl(clean)) {
        return {
          // Fake Pi runs entirely in the browser: no session exists, so it
          // reports an admin identity rather than gating the UI.
          me: async () => ({ id: "sim", name: "Simulator", role: "ADMIN", faceEnrolled: false }),
          logout: async () => ({ ok: true }),
          pairRedeem: async () => ({ id: "sim", name: "Simulator", role: "ADMIN" }),
          pairCreate: async () => ({ ok: true, code: "SIMULATED" }),
          deleteFace: async () => ({ ok: true }),
          status: async () => ({
            online: true,
            lockState: simRef.current.locked ? "locked" : "unlocked",
            ignitionOn: !!simRef.current.ignitionOn,
            deviceName: "FakePi-Browser",
            battery: simRef.current.battery,
            signal: simRef.current.signal,
            lastSeen: Date.now(),
          }),
          unlock: async () => {
            updateSim((s) => ({
              ...s,
              locked: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "unlock", ok: true, detail: "fake_pi_manual" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          lock: async () => {
            updateSim((s) => ({
              ...s,
              locked: true,
              ignitionOn: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "fake_pi_manual" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          ignitionStop: async () => {
            updateSim((s) => ({
              ...s,
              ignitionOn: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: "fake_stop" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          fullReset: async () => {
            updateSim((s) => ({
              ...s,
              locked: true,
              ignitionOn: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "reset", ok: true, detail: "fake_full_reset" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          users: async () => simRef.current.users,
          faceStatus: async () => ({ enrolled: [], count: 0 }),
          addUser: async (name) => {
            const displayName = name.trim();
            const existing = simRef.current.users.find(
              (u) => u.name.trim().toLocaleLowerCase() === displayName.toLocaleLowerCase(),
            );
            if (existing) throw new Error("active user with that name already exists");
            const u = { id: genId("u"), name: displayName, createdAt: Date.now(), faceAccess: true };
            updateSim((s) => ({
              ...s,
              users: [u, ...s.users],
              logs: [{ id: genId("log"), ts: Date.now(), type: "enroll", ok: true, detail: `Fake Pi added ${name}` }, ...s.logs].slice(0, 80),
            }));
            return u;
          },
          delUser: async (id) => {
            updateSim((s) => {
              const removed = s.users.find((u) => u.id === id);
              return {
                ...s,
                users: s.users.filter((u) => u.id !== id),
                logs: [
                  { id: genId("log"), ts: Date.now(), type: "delete_user", ok: true, detail: `Fake Pi removed ${removed?.name || id}` },
                  ...s.logs,
                ].slice(0, 80),
              };
            });
            return { ok: true };
          },
          setAccess: async (id, allowed) => {
            updateSim((s) => ({
              ...s,
              users: s.users.map((u) => (u.id === id ? { ...u, faceAccess: allowed } : u)),
              logs: [{ id: genId("log"), ts: Date.now(), type: "access_change", ok: true, detail: `${allowed ? "allowed" : "blocked"}:${id}` }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          logs: async () => simRef.current.logs,
          verifyLog: async () => ({ ok: true }),
          getSettings: async () => simRef.current.settings,
          saveSettings: async (next) => {
            updateSim((s) => ({
              ...s,
              settings: { ...s.settings, ...next },
              logs: [{ id: genId("log"), ts: Date.now(), type: "settings", ok: true, detail: "Fake Pi settings updated" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          scanStart: async ({ purpose = "unlock", expectedUser = null } = {}) => {
            const user = expectedUser || simRef.current.users[0]?.name || "Demo Driver";
            if (purpose === "ignition") {
              if (simRef.current.locked) {
                return {
                  ok: false,
                  session_id: genId("scan"),
                  state: "denied",
                  purpose,
                  user,
                  score: 0.81,
                  face_count: 1,
                  message: "Fake Pi denied ignition because the device is locked.",
                  window: { matches: 0, needed: 6, size: 10 },
                };
              }
              updateSim((s) => ({
                ...s,
                ignitionOn: true,
                logs: [{ id: genId("log"), ts: Date.now(), type: "face_scan", ok: true, detail: `Fake ignition granted for ${user}` }, ...s.logs].slice(0, 80),
              }));
            } else {
              updateSim((s) => ({
                ...s,
                locked: false,
                logs: [{ id: genId("log"), ts: Date.now(), type: "face_scan", ok: true, detail: `Fake unlock granted for ${user}` }, ...s.logs].slice(0, 80),
              }));
            }
            return {
              ok: true,
              session_id: genId("scan"),
              state: "granted",
              purpose,
              user,
              score: 0.82,
              face_count: 1,
              message: "Fake Pi scan granted.",
              window: { matches: 6, needed: 6, size: 10 },
            };
          },
          scanStatus: async (sessionId) => ({
            ok: true,
            session_id: sessionId,
            state: "granted",
            user: simRef.current.users[0]?.name || "Demo Driver",
            score: 0.82,
            face_count: 1,
            window: { matches: 6, needed: 6, size: 10 },
          }),
          scanCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
          scanSample: async (sessionId) => ({ ok: true, session_id: sessionId, state: "scanning" }),
          piEnrollStart: async ({ name } = {}) => ({
            ok: true,
            session_id: genId("enroll"),
            state: "completed",
            user: (name || "Demo Driver").trim(),
            count: 10,
            samples_needed: 10,
            source: "pi_camera",
            recognition_available: false,
          }),
          piEnrollStatus: async (sessionId) => ({
            ok: true,
            session_id: sessionId,
            state: "completed",
            count: 10,
            samples_needed: 10,
            source: "pi_camera",
            recognition_available: false,
          }),
          piEnrollCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
          piEnrollSample: async (sessionId) => ({
            ok: true,
            session_id: sessionId,
            state: "capturing",
            count: 10,
            samples_needed: 10,
            source: "client_camera",
          }),
          piEnrollFinish: async (sessionId) => ({
            ok: true,
            session_id: sessionId,
            state: "completed",
            count: 10,
            samples_needed: 10,
            source: "client_camera",
            recognition_available: false,
          }),
        };
      }
      if (!clean) {
        const missingBaseUrl = async () => {
          throw new Error("Base URL required in Device mode.");
        };
        return {
          status: missingBaseUrl,
          unlock: missingBaseUrl,
          lock: missingBaseUrl,
          ignitionStop: missingBaseUrl,
          fullReset: missingBaseUrl,
          users: missingBaseUrl,
          faceStatus: missingBaseUrl,
          addUser: missingBaseUrl,
          delUser: missingBaseUrl,
          setAccess: missingBaseUrl,
          logs: missingBaseUrl,
          verifyLog: missingBaseUrl,
          getSettings: missingBaseUrl,
          saveSettings: missingBaseUrl,
          scanStart: missingBaseUrl,
          scanStatus: missingBaseUrl,
          scanCancel: missingBaseUrl,
          scanSample: missingBaseUrl,
          piEnrollStart: missingBaseUrl,
          piEnrollStatus: missingBaseUrl,
          piEnrollCancel: missingBaseUrl,
          piEnrollSample: missingBaseUrl,
          piEnrollFinish: missingBaseUrl,
        };
      }
      const apiJson = (url, opts) => fetchJson(url, opts);
      const apiForm = (url, form) => fetchFormJson(url, form);

      return {
        me: () => apiJson(`${clean}/api/me`),
        logout: () => apiJson(`${clean}/api/auth/logout`, { method: "POST" }),
        pairRedeem: (code) =>
          apiJson(`${clean}/api/pair/redeem`, {
            method: "POST",
            body: JSON.stringify({ code }),
          }),
        pairCreate: (userId) =>
          apiJson(`${clean}/api/pair/create`, {
            method: "POST",
            body: JSON.stringify({ user_id: userId }),
          }),
        status: () => apiJson(`${clean}/api/status`),
        unlock: () =>
          apiJson(`${clean}/api/unlock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
        lock: () =>
          apiJson(`${clean}/api/lock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
        ignitionStop: () => apiJson(`${clean}/api/ignition/stop`, { method: "POST" }),
        fullReset: () => apiJson(`${clean}/api/full-reset`, { method: "POST" }),
        users: () => apiJson(`${clean}/api/users`),
        faceStatus: () => apiJson(`${clean}/api/face-status`),
        addUser: (name) =>
          apiJson(`${clean}/api/users`, { method: "POST", body: JSON.stringify({ name }) }),
        delUser: (id) =>
          apiJson(`${clean}/api/users/${encodeURIComponent(id)}`, { method: "DELETE" }),
        deleteFace: (id) =>
          apiJson(`${clean}/api/users/${encodeURIComponent(id)}/face`, { method: "DELETE" }),
        setAccess: (id, allowed) =>
          apiJson(`${clean}/api/users/${encodeURIComponent(id)}/access`, { method: "PATCH", body: JSON.stringify({ allowed }) }),
        logs: () => apiJson(`${clean}/api/logs`),
        verifyLog: (result, detail, user_id) =>
          apiJson(`${clean}/api/verify-log`, { method: "POST", body: JSON.stringify({ result, detail, user_id }) }),
        getSettings: () => apiJson(`${clean}/api/settings`),
        saveSettings: (s) =>
          apiJson(`${clean}/api/settings`, { method: "POST", body: JSON.stringify(s) }),
        scanStart: (payload = {}) =>
          apiJson(`${clean}/api/scan/start`, { method: "POST", body: JSON.stringify(payload) }),
        scanStatus: (sessionId) =>
          apiJson(`${clean}/api/scan/status?session_id=${encodeURIComponent(sessionId)}`),
        scanCancel: (sessionId) =>
          apiJson(`${clean}/api/scan/cancel`, { method: "POST", body: JSON.stringify({ session_id: sessionId }) }),
        scanSample: (sessionId, blob) => {
          const form = new FormData();
          form.append("session_id", sessionId);
          form.append("image", blob, "frame.jpg");
          return apiForm(`${clean}/api/scan/sample`, form);
        },
        piEnrollStart: (payload = {}) =>
          apiJson(`${clean}/api/enroll/start`, { method: "POST", body: JSON.stringify(payload) }),
        piEnrollStatus: (sessionId) =>
          apiJson(`${clean}/api/enroll/status?session_id=${encodeURIComponent(sessionId)}`),
        piEnrollCancel: (sessionId) =>
          apiJson(`${clean}/api/enroll/cancel`, { method: "POST", body: JSON.stringify({ session_id: sessionId }) }),
        piEnrollSample: (sessionId, blob) => {
          const form = new FormData();
          form.append("session_id", sessionId);
          form.append("image", blob, "sample.jpg");
          return apiForm(`${clean}/api/enroll/sample`, form);
        },
        piEnrollFinish: (sessionId) =>
          apiJson(`${clean}/api/enroll/finish`, { method: "POST", body: JSON.stringify({ session_id: sessionId }) }),
      };
    }

    // SIM
    return {
      // The simulator has no server and therefore no session; it reports an
      // admin identity so the whole UI stays reachable while developing.
      me: async () => ({ id: "sim", name: "Simulator", role: "ADMIN", faceEnrolled: false }),
      logout: async () => ({ ok: true }),
      pairRedeem: async () => ({ id: "sim", name: "Simulator", role: "ADMIN" }),
      pairCreate: async () => ({ ok: true, code: "SIMULATED" }),
      deleteFace: async () => ({ ok: true }),
      status: async () => ({
        online: true,
        lockState: simRef.current.locked ? "locked" : "unlocked",
        ignitionOn: !!simRef.current.ignitionOn,
        deviceName: simRef.current.deviceName,
        battery: simRef.current.battery,
        signal: simRef.current.signal,
        lastSeen: Date.now(),
      }),
      unlock: async () => {
        updateSim((s) => ({
          ...s,
          locked: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "unlock", ok: true, detail: "manual_ui" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      lock: async () => {
        updateSim((s) => ({
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "manual_ui" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      ignitionStop: async () => {
        updateSim((s) => ({
          ...s,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: "stop" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      fullReset: async () => {
        updateSim((s) => ({
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "reset", ok: true, detail: "full_reset" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      users: async () => simRef.current.users,
      faceStatus: async () => ({ enrolled: [], count: 0 }),
      addUser: async (name) => {
        const displayName = name.trim();
        const existing = simRef.current.users.find(
          (u) => u.name.trim().toLocaleLowerCase() === displayName.toLocaleLowerCase(),
        );
        if (existing) throw new Error("active user with that name already exists");
        const u = { id: genId("u"), name: displayName, createdAt: Date.now(), faceAccess: true };
        updateSim((s) => ({
          ...s,
          users: [u, ...s.users],
          logs: [{ id: genId("log"), ts: Date.now(), type: "enroll", ok: true, detail: `Added ${name}` }, ...s.logs].slice(0, 80),
        }));
        return u;
      },
      delUser: async (id) => {
        updateSim((s) => {
          const removed = s.users.find((x) => x.id === id);
          const label = removed?.name?.trim() || id;
          return {
            ...s,
            users: s.users.filter((x) => x.id !== id),
            logs: [{ id: genId("log"), ts: Date.now(), type: "delete_user", ok: true, detail: `Deleted ${label}` }, ...s.logs].slice(0, 80),
          };
        });
        return { ok: true };
      },
      setAccess: async (id, allowed) => {
        updateSim((s) => ({
          ...s,
          users: s.users.map((u) => u.id === id ? { ...u, faceAccess: allowed } : u),
          logs: [{ id: genId("log"), ts: Date.now(), type: "access_change", ok: true, detail: `${allowed ? "granted" : "revoked"} for ${id}` }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      verifyLog: async () => ({ ok: true }),
      logs: async () => simRef.current.logs,
      getSettings: async () => simRef.current.settings,
      saveSettings: async (next) => {
        updateSim((s) => ({
          ...s,
          settings: { ...s.settings, ...next },
          logs: [{ id: genId("log"), ts: Date.now(), type: "settings", ok: true, detail: "Updated settings" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      scanStart: async ({ purpose = "unlock", expectedUser = null } = {}) => {
        const user = expectedUser || simRef.current.users[0]?.name || "Demo Driver";
        if (purpose === "ignition") {
          updateSim((s) => ({
            ...s,
            ignitionOn: true,
            logs: [{ id: genId("log"), ts: Date.now(), type: "face_scan", ok: true, detail: `Ignition granted for ${user}` }, ...s.logs].slice(0, 80),
          }));
        } else {
          updateSim((s) => ({
            ...s,
            locked: false,
            logs: [{ id: genId("log"), ts: Date.now(), type: "face_scan", ok: true, detail: `Unlock granted for ${user}` }, ...s.logs].slice(0, 80),
          }));
        }
        return {
          ok: true,
          session_id: genId("scan"),
          state: "granted",
          purpose,
          user,
          score: 0.82,
          face_count: 1,
          message: "simulated_scan_granted",
          window: { matches: 6, needed: 6, size: 10 },
        };
      },
      scanStatus: async (sessionId) => ({
        ok: true,
        session_id: sessionId,
        state: "granted",
        user: simRef.current.users[0]?.name || "Demo Driver",
        score: 0.82,
        face_count: 1,
        window: { matches: 6, needed: 6, size: 10 },
      }),
      scanCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
      scanSample: async (sessionId) => ({ ok: true, session_id: sessionId, state: "scanning" }),
      piEnrollStart: async ({ name } = {}) => {
        const displayName = (name || "Demo Driver").trim();
        return {
          ok: true,
          session_id: genId("enroll"),
          state: "completed",
          user: displayName,
          count: 10,
          samples_needed: 10,
          source: "pi_camera",
          recognition_available: false,
        };
      },
      piEnrollStatus: async (sessionId) => ({
        ok: true,
        session_id: sessionId,
        state: "completed",
        count: 10,
        samples_needed: 10,
        source: "pi_camera",
        recognition_available: false,
      }),
      piEnrollCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
      piEnrollSample: async (sessionId) => ({
        ok: true,
        session_id: sessionId,
        state: "capturing",
        count: 10,
        samples_needed: 10,
        source: "client_camera",
      }),
      piEnrollFinish: async (sessionId) => ({
        ok: true,
        session_id: sessionId,
        state: "completed",
        count: 10,
        samples_needed: 10,
        source: "client_camera",
        recognition_available: false,
      }),
    };
  }, [mode, baseUrl, setSim]);
}
