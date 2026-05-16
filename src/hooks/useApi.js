import { useMemo } from "react";
import { genId } from "../utils/helpers";

async function fetchJson(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    throw new Error(`Expected JSON response from ${url}`);
  }
  return res.json();
}

function isMockPiUrl(url) {
  const raw = (url || "").trim();
  if (!raw) return false;
  try {
    return new URL(raw).port === "5055";
  } catch {
    return /(^|:)5055(\/|$)/.test(raw);
  }
}

export default function useApi(mode, baseUrl, sim, setSim) {
  return useMemo(() => {
    if (mode === "device") {
      const clean = (baseUrl || "").trim().replace(/\/$/, "");
      if (isMockPiUrl(clean)) {
        return {
          status: async () => ({
            online: true,
            lockState: sim.locked ? "locked" : "unlocked",
            ignitionOn: !!sim.ignitionOn,
            deviceName: "FakePi-5055",
            battery: sim.battery,
            signal: sim.signal,
            lastSeen: Date.now(),
          }),
          unlock: async () => {
            setSim((s) => ({
              ...s,
              locked: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "unlock", ok: true, detail: "fake_pi_manual" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          lock: async () => {
            setSim((s) => ({
              ...s,
              locked: true,
              ignitionOn: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "fake_pi_manual" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          ignitionStart: async () => {
            if (sim.locked) throw new Error("device is locked");
            setSim((s) => ({
              ...s,
              ignitionOn: true,
              logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: "fake_start" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          ignitionStop: async () => {
            setSim((s) => ({
              ...s,
              ignitionOn: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: "fake_stop" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          fullReset: async () => {
            setSim((s) => ({
              ...s,
              locked: true,
              ignitionOn: false,
              logs: [{ id: genId("log"), ts: Date.now(), type: "reset", ok: true, detail: "fake_full_reset" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          users: async () => sim.users,
          addUser: async (name) => {
            const existing = sim.users.find((u) => u.name === name);
            if (existing) return existing;
            const u = { id: genId("u"), name, createdAt: Date.now(), faceAccess: true };
            setSim((s) => ({
              ...s,
              users: [u, ...s.users],
              logs: [{ id: genId("log"), ts: Date.now(), type: "enroll", ok: true, detail: `Fake Pi added ${name}` }, ...s.logs].slice(0, 80),
            }));
            return u;
          },
          delUser: async (id) => {
            setSim((s) => {
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
            setSim((s) => ({
              ...s,
              users: s.users.map((u) => (u.id === id ? { ...u, faceAccess: allowed } : u)),
              logs: [{ id: genId("log"), ts: Date.now(), type: "access_change", ok: true, detail: `${allowed ? "allowed" : "blocked"}:${id}` }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          setEmbedding: async () => ({ ok: true }),
          logs: async () => sim.logs,
          verifyLog: async () => ({ ok: true }),
          getSettings: async () => sim.settings,
          saveSettings: async (next) => {
            setSim((s) => ({
              ...s,
              settings: { ...s.settings, ...next },
              logs: [{ id: genId("log"), ts: Date.now(), type: "settings", ok: true, detail: "Fake Pi settings updated" }, ...s.logs].slice(0, 80),
            }));
            return { ok: true };
          },
          scanStart: async ({ purpose = "unlock", expectedUser = null } = {}) => {
            const user = expectedUser || sim.users[0]?.name || "Demo Driver";
            if (purpose === "ignition") {
              if (sim.locked) {
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
              setSim((s) => ({
                ...s,
                ignitionOn: true,
                logs: [{ id: genId("log"), ts: Date.now(), type: "face_scan", ok: true, detail: `Fake ignition granted for ${user}` }, ...s.logs].slice(0, 80),
              }));
            } else {
              setSim((s) => ({
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
            user: sim.users[0]?.name || "Demo Driver",
            score: 0.82,
            face_count: 1,
            window: { matches: 6, needed: 6, size: 10 },
          }),
          scanCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
          piEnrollStart: async ({ name } = {}) => ({
            ok: true,
            session_id: genId("enroll"),
            state: "completed",
            user: (name || "Demo Driver").trim(),
            count: 10,
            samples_needed: 10,
            source: "pi_camera",
          }),
          piEnrollStatus: async (sessionId) => ({
            ok: true,
            session_id: sessionId,
            state: "completed",
            count: 10,
            samples_needed: 10,
            source: "pi_camera",
          }),
          piEnrollCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
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
          ignitionStart: missingBaseUrl,
          ignitionStop: missingBaseUrl,
          fullReset: missingBaseUrl,
          users: missingBaseUrl,
          addUser: missingBaseUrl,
          delUser: missingBaseUrl,
          setAccess: missingBaseUrl,
          setEmbedding: missingBaseUrl,
          logs: missingBaseUrl,
          verifyLog: missingBaseUrl,
          getSettings: missingBaseUrl,
          saveSettings: missingBaseUrl,
        };
      }
      return {
        status: () => fetchJson(`${clean}/api/status`),
        unlock: () =>
          fetchJson(`${clean}/api/unlock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
        lock: () =>
          fetchJson(`${clean}/api/lock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
        ignitionStart: () => fetchJson(`${clean}/api/ignition/start`, { method: "POST" }),
        ignitionStop: () => fetchJson(`${clean}/api/ignition/stop`, { method: "POST" }),
        fullReset: () => fetchJson(`${clean}/api/full-reset`, { method: "POST" }),
        users: () => fetchJson(`${clean}/api/users`),
        addUser: (name) =>
          fetchJson(`${clean}/api/users`, { method: "POST", body: JSON.stringify({ name }) }),
        delUser: (id) =>
          fetchJson(`${clean}/api/users/${encodeURIComponent(id)}`, { method: "DELETE" }),
        setAccess: (id, allowed) =>
          fetchJson(`${clean}/api/users/${encodeURIComponent(id)}/access`, { method: "PATCH", body: JSON.stringify({ allowed }) }),
        setEmbedding: (id, blob) =>
          fetchJson(`${clean}/api/users/${encodeURIComponent(id)}/embedding`, { method: "PATCH", body: JSON.stringify({ blob }) }),
        logs: () => fetchJson(`${clean}/api/logs`),
        verifyLog: (result, detail, user_id) =>
          fetchJson(`${clean}/api/verify-log`, { method: "POST", body: JSON.stringify({ result, detail, user_id }) }),
        getSettings: () => fetchJson(`${clean}/api/settings`),
        saveSettings: (s) =>
          fetchJson(`${clean}/api/settings`, { method: "POST", body: JSON.stringify(s) }),
        scanStart: (payload = {}) =>
          fetchJson(`${clean}/api/scan/start`, { method: "POST", body: JSON.stringify(payload) }),
        scanStatus: (sessionId) =>
          fetchJson(`${clean}/api/scan/status?session_id=${encodeURIComponent(sessionId)}`),
        scanCancel: (sessionId) =>
          fetchJson(`${clean}/api/scan/cancel`, { method: "POST", body: JSON.stringify({ session_id: sessionId }) }),
        piEnrollStart: (payload = {}) =>
          fetchJson(`${clean}/api/enroll/start`, { method: "POST", body: JSON.stringify(payload) }),
        piEnrollStatus: (sessionId) =>
          fetchJson(`${clean}/api/enroll/status?session_id=${encodeURIComponent(sessionId)}`),
        piEnrollCancel: (sessionId) =>
          fetchJson(`${clean}/api/enroll/cancel`, { method: "POST", body: JSON.stringify({ session_id: sessionId }) }),
      };
    }

    // SIM
    return {
      status: async () => ({
        online: true,
        lockState: sim.locked ? "locked" : "unlocked",
        ignitionOn: !!sim.ignitionOn,
        deviceName: sim.deviceName,
        battery: sim.battery,
        signal: sim.signal,
        lastSeen: Date.now(),
      }),
      unlock: async () => {
        setSim((s) => ({
          ...s,
          locked: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "unlock", ok: true, detail: "manual_ui" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      lock: async () => {
        setSim((s) => ({
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "manual_ui" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      ignitionStart: async () => {
        setSim((s) => ({
          ...s,
          ignitionOn: true,
          logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: "start" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      ignitionStop: async () => {
        setSim((s) => ({
          ...s,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: "stop" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      fullReset: async () => {
        setSim((s) => ({
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "reset", ok: true, detail: "full_reset" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      users: async () => sim.users,
      addUser: async (name) => {
        const u = { id: genId("u"), name, createdAt: Date.now() };
        setSim((s) => ({
          ...s,
          users: [u, ...s.users],
          logs: [{ id: genId("log"), ts: Date.now(), type: "enroll", ok: true, detail: `Added ${name}` }, ...s.logs].slice(0, 80),
        }));
        return u;
      },
      delUser: async (id) => {
        setSim((s) => {
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
        setSim((s) => ({
          ...s,
          users: s.users.map((u) => u.id === id ? { ...u, faceAccess: allowed } : u),
          logs: [{ id: genId("log"), ts: Date.now(), type: "access_change", ok: true, detail: `${allowed ? "granted" : "revoked"} for ${id}` }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      setEmbedding: async () => ({ ok: true }),
      verifyLog: async () => ({ ok: true }),
      logs: async () => sim.logs,
      getSettings: async () => sim.settings,
      saveSettings: async (next) => {
        setSim((s) => ({
          ...s,
          settings: { ...s.settings, ...next },
          logs: [{ id: genId("log"), ts: Date.now(), type: "settings", ok: true, detail: "Updated settings" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      scanStart: async ({ purpose = "unlock", expectedUser = null } = {}) => {
        const user = expectedUser || sim.users[0]?.name || "Demo Driver";
        if (purpose === "ignition") {
          setSim((s) => ({
            ...s,
            ignitionOn: true,
            logs: [{ id: genId("log"), ts: Date.now(), type: "face_scan", ok: true, detail: `Ignition granted for ${user}` }, ...s.logs].slice(0, 80),
          }));
        } else {
          setSim((s) => ({
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
        user: sim.users[0]?.name || "Demo Driver",
        score: 0.82,
        face_count: 1,
        window: { matches: 6, needed: 6, size: 10 },
      }),
      scanCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
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
        };
      },
      piEnrollStatus: async (sessionId) => ({
        ok: true,
        session_id: sessionId,
        state: "completed",
        count: 10,
        samples_needed: 10,
        source: "pi_camera",
      }),
      piEnrollCancel: async (sessionId) => ({ ok: true, session_id: sessionId, state: "cancelled" }),
    };
  }, [mode, baseUrl, sim, setSim]);
}
