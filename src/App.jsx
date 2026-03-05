import React, { useEffect, useMemo, useState } from "react";

const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const genId = (p = "id") => `${p}_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
const fmt = (ts) => new Date(ts).toLocaleString();

function Badge({ children }) {
  return (
    <span className="inline-flex items-center rounded-full border border-neutral-200 bg-white px-2.5 py-1 text-xs text-neutral-700">
      {children}
    </span>
  );
}

function Card({ children }) {
  return (
    <div className="rounded-3xl bg-white shadow-sm ring-1 ring-neutral-200">
      <div className="p-5">{children}</div>
    </div>
  );
}

function Btn({ variant = "primary", disabled, onClick, children }) {
  const base =
    "inline-flex items-center justify-center rounded-2xl px-4 py-2 text-sm font-medium transition active:scale-[0.99] disabled:opacity-50 disabled:cursor-not-allowed";
  const styles =
    variant === "secondary"
      ? "bg-neutral-100 text-neutral-900 hover:bg-neutral-200"
      : variant === "danger"
      ? "bg-red-600 text-white hover:bg-red-700"
      : "bg-neutral-900 text-white hover:bg-neutral-800";
  return (
    <button className={`${base} ${styles}`} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function Input({ value, onChange, placeholder, type = "text", disabled }) {
  return (
    <input
      type={type}
      value={value}
      disabled={disabled}
      onChange={onChange}
      placeholder={placeholder}
      className="w-full rounded-2xl border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-neutral-300 disabled:opacity-60"
    />
  );
}

function Switch({ checked, onChange }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`h-7 w-12 rounded-full p-1 transition ${
        checked ? "bg-neutral-900" : "bg-neutral-200"
      }`}
      aria-pressed={checked}
    >
      <div
        className={`h-5 w-5 rounded-full bg-white transition ${
          checked ? "translate-x-5" : "translate-x-0"
        }`}
      />
    </button>
  );
}

function TabBtn({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-2xl px-3 py-2 text-sm font-medium ring-1 transition ${
        active
          ? "bg-neutral-900 text-white ring-neutral-900"
          : "bg-white text-neutral-800 ring-neutral-200 hover:bg-neutral-50"
      }`}
    >
      {children}
    </button>
  );
}

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

export default function App() {
  const [mode, setMode] = useState(() => localStorage.getItem("mode") || "sim"); // sim | device
  const [baseUrl, setBaseUrl] = useState(() => localStorage.getItem("baseUrl") || "http://192.168.4.1");
  const [tab, setTab] = useState("control");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);

  const [sim, setSim] = useState(() => {
    const raw = localStorage.getItem("sim");
    if (raw) return JSON.parse(raw);
    return {
      deviceName: "FaceLock-Pi",
      locked: true,
      battery: 100,
      signal: 5,
      users: [
        { id: "u_mei", name: "Mei", createdAt: Date.now() - 1000 * 60 * 60 * 3 },
        { id: "u_alex", name: "Alex", createdAt: Date.now() - 1000 * 60 * 60 * 24 },
      ],
      logs: [
        { id: "l1", ts: Date.now() - 1000 * 60 * 10, type: "boot", ok: true, detail: "Device started" },
        { id: "l2", ts: Date.now() - 1000 * 60 * 6, type: "lock", ok: true, detail: "Locked" },
      ],
      settings: { autoRelockSeconds: 10, liveness: true, failLockout: true, lockoutAfter: 5 },
    };
  });

  useEffect(() => {
    localStorage.setItem("mode", mode);
    localStorage.setItem("baseUrl", baseUrl);
    localStorage.setItem("sim", JSON.stringify(sim));
  }, [mode, baseUrl, sim]);

  const [status, setStatus] = useState({
    online: true,
    lockState: "locked",
    deviceName: "",
    battery: 0,
    signal: 0,
    lastSeen: Date.now(),
  });

  const locked = mode === "sim" ? sim.locked : status.lockState === "locked";

  const api = useMemo(() => {
    if (mode === "device") {
      const clean = baseUrl.replace(/\/$/, "");
      return {
        status: () => fetchJson(`${clean}/api/status`),
        unlock: () => fetchJson(`${clean}/api/unlock`, { method: "POST", body: JSON.stringify({ reason: "manual_ui" }) }),
        users: () => fetchJson(`${clean}/api/users`),
        addUser: (name) => fetchJson(`${clean}/api/users`, { method: "POST", body: JSON.stringify({ name }) }),
        delUser: (id) => fetchJson(`${clean}/api/users/${encodeURIComponent(id)}`, { method: "DELETE" }),
        logs: () => fetchJson(`${clean}/api/logs`),
        saveSettings: (s) => fetchJson(`${clean}/api/settings`, { method: "POST", body: JSON.stringify(s) }),
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
        setSim((s) => ({
          ...s,
          users: s.users.filter((x) => x.id !== id),
          logs: [{ id: genId("log"), ts: Date.now(), type: "delete_user", ok: true, detail: `Deleted ${id}` }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
      logs: async () => sim.logs,
      saveSettings: async (next) => {
        setSim((s) => ({
          ...s,
          settings: { ...s.settings, ...next },
          logs: [{ id: genId("log"), ts: Date.now(), type: "settings", ok: true, detail: "Updated settings" }, ...s.logs].slice(0, 80),
        }));
        return { ok: true };
      },
    };
  }, [mode, baseUrl, sim]);

  const popToast = (type, title, msg) => {
    setToast({ id: genId("t"), type, title, msg });
    setTimeout(() => setToast(null), 2500);
  };

  const refresh = async () => {
    setBusy(true);
    try {
      const s = await api.status();
      setStatus(s);
      popToast("ok", "Refreshed", mode === "device" ? `Connected to ${baseUrl}` : "Simulation updated");
    } catch (e) {
      setStatus((p) => ({ ...p, online: false }));
      popToast("err", "Connection failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, baseUrl]);

  const doUnlock = async () => {
    setBusy(true);
    try {
      await api.unlock();
      await refresh();
      popToast("ok", "Unlocked", "Device reports unlocked.");
    } catch (e) {
      popToast("err", "Unlock failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  const doLockSim = () => {
    if (mode !== "sim") return popToast("info", "Disabled", "Lock is SIM-only for safety.");
    setSim((s) => ({
      ...s,
      locked: true,
      logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "Locked (sim)" }, ...s.logs].slice(0, 80),
    }));
  };

  const [name, setName] = useState("");

  const addUser = async () => {
    const n = name.trim();
    if (!n) return popToast("err", "Name required", "Please enter a user name.");
    setBusy(true);
    try {
      await api.addUser(n);
      setName("");
      popToast("ok", "Enrolled", `Added ${n}`);
    } catch (e) {
      popToast("err", "Enroll failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  const delUser = async (id) => {
    if (!confirm("Remove this user?")) return;
    setBusy(true);
    try {
      await api.delUser(id);
      popToast("ok", "Removed", "User deleted.");
    } catch (e) {
      popToast("err", "Delete failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  const [settings, setSettings] = useState(sim.settings);

  useEffect(() => {
    if (mode === "sim") setSettings(sim.settings);
  }, [mode, sim.settings]);

  const saveSettings = async () => {
    setBusy(true);
    try {
      await api.saveSettings(settings);
      popToast("ok", "Saved", "Settings updated.");
    } catch (e) {
      popToast("err", "Save failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      {toast ? (
        <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2">
          <div
            className={`rounded-2xl px-4 py-3 shadow-lg ring-1 ${
              toast.type === "err"
                ? "bg-white ring-red-200"
                : toast.type === "ok"
                ? "bg-white ring-emerald-200"
                : "bg-white ring-neutral-200"
            }`}
          >
            <div className="text-sm font-semibold">{toast.title}</div>
            {toast.msg ? <div className="mt-1 text-xs text-neutral-600">{toast.msg}</div> : null}
          </div>
        </div>
      ) : null}

      <div className="mx-auto max-w-5xl px-4 py-6 md:px-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Face Unlock Car Lock</h1>
            <p className="mt-2 max-w-2xl text-sm text-neutral-600">
              v1 control panel: connect, enroll users, unlock, view logs, adjust settings. Switch to Device API mode when your Pi is ready.
            </p>
          </div>
          <Btn variant="secondary" disabled={busy} onClick={refresh}>
            {busy ? "..." : "Refresh"}
          </Btn>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
          <Card>
            <div className="text-xs text-neutral-500">Mode</div>
            <div className="mt-1 flex items-center justify-between">
              <div className="text-sm font-medium">{mode === "device" ? "Device API" : "Simulation"}</div>
              <Badge>v1</Badge>
            </div>
          </Card>
          <Card>
            <div className="text-xs text-neutral-500">Device</div>
            <div className="mt-1 flex items-center justify-between">
              <div className="text-sm font-medium">{mode === "sim" ? sim.deviceName : status.deviceName || "(unknown)"}</div>
              <Badge>{status.online ? "Online" : "Offline"}</Badge>
            </div>
          </Card>
          <Card>
            <div className="text-xs text-neutral-500">Safety</div>
            <div className="mt-1 text-sm font-medium">Local-only, no cloud</div>
          </Card>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="md:col-span-2">
            <Card>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs text-neutral-500">Current state</div>
                  <div className="mt-1 text-lg font-semibold">{locked ? "Locked" : "Unlocked"}</div>
                  <div className="mt-2 text-sm text-neutral-600">Tap to unlock (demo). In Device API mode this sends a command to your Pi.</div>
                </div>
                <div className="flex items-center gap-2">
                  {locked ? (
                    <Btn disabled={busy} onClick={doUnlock}>
                      Unlock
                    </Btn>
                  ) : (
                    <Btn variant="secondary" disabled={busy} onClick={doLockSim}>
                      Lock (sim)
                    </Btn>
                  )}
                </div>
              </div>
            </Card>

            <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs text-neutral-500">Battery</div>
                    <div className="mt-1 text-lg font-semibold">{mode === "sim" ? sim.battery : status.battery}%</div>
                  </div>
                  <Badge>Demo</Badge>
                </div>
                <div className="mt-3 h-2 w-full rounded-full bg-neutral-100">
                  <div
                    className="h-2 rounded-full bg-neutral-900"
                    style={{ width: `${mode === "sim" ? sim.battery : status.battery}%` }}
                  />
                </div>
              </Card>
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs text-neutral-500">Signal</div>
                    <div className="mt-1 text-lg font-semibold">{mode === "sim" ? sim.signal : status.signal}/5</div>
                  </div>
                  <Badge>{status.online ? "Online" : "Offline"}</Badge>
                </div>
                <div className="mt-2 text-sm text-neutral-600">Use same Wi-Fi router, or Pi hotspot later.</div>
              </Card>
            </div>
          </div>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">Connection</div>
                <div className="mt-1 text-xs text-neutral-600">Switch between Simulation and real device API.</div>
              </div>
              <Badge>LAN</Badge>
            </div>

            <div className="mt-4 space-y-4">
              <div className="flex items-center justify-between">
                <div className="text-sm">Device API mode</div>
                <Switch checked={mode === "device"} onChange={(v) => setMode(v ? "device" : "sim")} />
              </div>

              <div className={mode === "device" ? "" : "opacity-60"}>
                <div className="text-sm font-medium">Base URL</div>
                <div className="mt-2">
                  <Input
                    disabled={mode !== "device"}
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="http://192.168.4.1"
                  />
                </div>
                <div className="mt-2 text-xs text-neutral-500">Example: http://192.168.1.23 (your Pi)</div>
              </div>

              <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-4 text-sm">
                Demo story: Pair → Enroll → Unlock → Logs
              </div>
            </div>
          </Card>
        </div>

        <div className="mt-6 flex flex-wrap gap-2">
          <TabBtn active={tab === "control"} onClick={() => setTab("control")}>Control</TabBtn>
          <TabBtn active={tab === "users"} onClick={() => setTab("users")}>Users</TabBtn>
          <TabBtn active={tab === "logs"} onClick={() => setTab("logs")}>Logs</TabBtn>
          <TabBtn active={tab === "settings"} onClick={() => setTab("settings")}>Settings</TabBtn>
        </div>

        <div className="mt-4">
          {tab === "control" ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Card>
                <div className="text-sm font-semibold">Face scan</div>
                <div className="mt-1 text-xs text-neutral-600">v1 placeholder. Later: show camera preview + recognition result.</div>
                <div className="mt-4 rounded-2xl border border-neutral-200 bg-neutral-50 p-4 text-sm">
                  Camera area (UI only)
                </div>
              </Card>

              <Card>
                <div className="text-sm font-semibold">Quick actions</div>
                <div className="mt-3 grid gap-2">
                  <Btn disabled={busy} onClick={doUnlock}>Unlock</Btn>
                  <Btn variant="secondary" disabled={busy} onClick={() => setTab("users")}>Go to Users</Btn>
                  <Btn variant="secondary" disabled={busy} onClick={() => setTab("logs")}>Go to Logs</Btn>
                </div>
              </Card>
            </div>
          ) : null}

          {tab === "users" ? (
            mode === "device" ? (
              <Card>
                <div className="text-sm font-semibold">Users (Device API mode)</div>
                <div className="mt-2 text-sm text-neutral-600">
                  Implement <code>/api/users</code> (GET/POST/DELETE) on Pi, then this can become live.
                </div>
              </Card>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="md:col-span-2">
                  <Card>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-semibold">Enrolled users</div>
                        <div className="mt-1 text-xs text-neutral-600">Each user represents a stored face template.</div>
                      </div>
                      <Badge>{sim.users.length} users</Badge>
                    </div>

                    <div className="mt-4 flex gap-2">
                      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g., Mei)" />
                      <Btn disabled={busy} onClick={addUser}>Add</Btn>
                    </div>

                    <div className="mt-4 space-y-2">
                      {sim.users.map((u) => (
                        <div key={u.id} className="flex items-center justify-between rounded-2xl border border-neutral-200 px-4 py-3">
                          <div>
                            <div className="text-sm font-medium">{u.name}</div>
                            <div className="text-xs text-neutral-500">Added: {fmt(u.createdAt)}</div>
                          </div>
                          <Btn variant="danger" disabled={busy} onClick={() => delUser(u.id)}>Remove</Btn>
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>

                <Card>
                  <div className="text-sm font-semibold">Checklist</div>
                  <div className="mt-2 text-sm text-neutral-700 space-y-2">
                    <div>• Good lighting</div>
                    <div>• Multiple angles</div>
                    <div>• Liveness ON recommended</div>
                  </div>
                </Card>
              </div>
            )
          ) : null}

          {tab === "logs" ? (
            mode === "device" ? (
              <Card>
                <div className="text-sm font-semibold">Logs (Device API mode)</div>
                <div className="mt-2 text-sm text-neutral-600">
                  Implement <code>GET /api/logs</code> on Pi (store last ~50 events).
                </div>
              </Card>
            ) : (
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-semibold">Event log</div>
                    <div className="mt-1 text-xs text-neutral-600">Enroll/unlock/settings events.</div>
                  </div>
                  <Btn
                    variant="secondary"
                    onClick={() => setSim((s) => ({ ...s, logs: [] }))}
                  >
                    Clear
                  </Btn>
                </div>

                <div className="mt-4 space-y-2">
                  {sim.logs.length === 0 ? (
                    <div className="text-sm text-neutral-600">No events.</div>
                  ) : (
                    sim.logs.map((e) => (
                      <div key={e.id} className="rounded-2xl border border-neutral-200 px-4 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-sm font-medium">
                            <span className="mr-2 inline-flex rounded-full bg-neutral-100 px-2 py-0.5 text-xs">
                              {e.type}
                            </span>
                            {e.ok ? "OK" : "FAIL"}
                          </div>
                          <div className="text-xs text-neutral-500">{fmt(e.ts)}</div>
                        </div>
                        {e.detail ? <div className="mt-1 text-sm text-neutral-600">{e.detail}</div> : null}
                      </div>
                    ))
                  )}
                </div>
              </Card>
            )
          ) : null}

          {tab === "settings" ? (
            mode === "device" ? (
              <Card>
                <div className="text-sm font-semibold">Settings (Device API mode)</div>
                <div className="mt-2 text-sm text-neutral-600">
                  Implement <code>POST /api/settings</code> on Pi. Validate on device.
                </div>
              </Card>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Card>
                  <div className="text-sm font-semibold">Core settings</div>

                  <div className="mt-4 space-y-4">
                    <div>
                      <div className="text-sm font-medium">Auto re-lock (seconds)</div>
                      <div className="mt-2">
                        <Input
                          type="number"
                          value={settings.autoRelockSeconds}
                          onChange={(e) =>
                            setSettings((s) => ({ ...s, autoRelockSeconds: clamp(parseInt(e.target.value || "0", 10), 0, 600) }))
                          }
                        />
                      </div>
                      <div className="mt-1 text-xs text-neutral-500">0 = disabled. Recommended 5–15.</div>
                    </div>

                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium">Liveness detection</div>
                        <div className="text-xs text-neutral-600">Reduce photo spoofing (simplified in v1).</div>
                      </div>
                      <Switch checked={settings.liveness} onChange={(v) => setSettings((s) => ({ ...s, liveness: v }))} />
                    </div>

                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium">Fail lockout</div>
                        <div className="text-xs text-neutral-600">Temporary lock after repeated failures.</div>
                      </div>
                      <Switch checked={settings.failLockout} onChange={(v) => setSettings((s) => ({ ...s, failLockout: v }))} />
                    </div>

                    <div>
                      <div className="text-sm font-medium">Lockout after (failures)</div>
                      <div className="mt-2">
                        <Input
                          type="number"
                          value={settings.lockoutAfter}
                          onChange={(e) =>
                            setSettings((s) => ({ ...s, lockoutAfter: clamp(parseInt(e.target.value || "5", 10), 1, 20) }))
                          }
                        />
                      </div>
                    </div>

                    <div className="flex items-center justify-end gap-2">
                      <Btn
                        variant="secondary"
                        onClick={() => setSettings({ autoRelockSeconds: 10, liveness: true, failLockout: true, lockoutAfter: 5 })}
                      >
                        Reset
                      </Btn>
                      <Btn disabled={busy} onClick={saveSettings}>Save</Btn>
                    </div>
                  </div>
                </Card>

                <Card>
                  <div className="text-sm font-semibold">Demo notes</div>
                  <div className="mt-2 text-sm text-neutral-700 space-y-3">
                    <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-4">
                      <div className="text-xs text-neutral-500">Next step</div>
                      <div className="mt-1">Implement Pi endpoints, then switch to Device API mode.</div>
                    </div>
                    <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-4">
                      <div className="text-xs text-neutral-500">Security</div>
                      <div className="mt-1">Local network only, add pairing PIN in v2.</div>
                    </div>
                  </div>
                </Card>
              </div>
            )
          ) : null}
        </div>

        <div className="mt-10 text-xs text-neutral-500">
          <Badge>v1 UI</Badge> <span className="mx-2">•</span> Local-first (LAN) <span className="mx-2">•</span> Demo-ready
        </div>
      </div>
    </div>
  );
}