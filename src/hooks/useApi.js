import { useMemo } from "react";
import { genId } from "../utils/helpers";

async function fetchJson(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export default function useApi(mode, baseUrl, sim, setSim) {
  return useMemo(() => {
    if (mode === "device") {
      const clean = baseUrl.replace(/\/$/, "");
      return {
        status: () => fetchJson(`${clean}/api/status`),
        unlock: () =>
          fetchJson(`${clean}/api/unlock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
        lock: () =>
          fetchJson(`${clean}/api/lock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
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
      };
    }

    // SIM
    return {
      status: async () => ({
        online: true,
        lockState: sim.locked ? "locked" : "unlocked",
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
          logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "manual_ui" }, ...s.logs].slice(0, 80),
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
    };
  }, [mode, baseUrl, sim, setSim]);
}
