import { useEffect } from "react";
import { genId } from "../utils/helpers";

export default function useAppActions(state) {
  const {
    mode,
    baseUrl,
    setBusy,
    popToast,
    setSim,
    setStatus,
    api,
    name,
    setName,
    settings,
    setSettings,
    setDeviceUsers,
    setDeviceLogs,
    faceApiUrl,
    sim,
    deviceUsers,
    setSimFaceAccessAllowed,
  } = state;

  const refresh = async (opts = {}) => {
    const silent = !!opts.silent;
    setBusy(true);
    try {
      const s = await api.status();
      setStatus(s);
      if (mode === "device") {
        const [users, logs, remoteSettings] = await Promise.all([
          api.users(),
          api.logs(),
          api.getSettings(),
        ]);
        setDeviceUsers(users);
        setDeviceLogs(logs);
        setSettings((prev) => ({ ...prev, ...remoteSettings }));
      }
      if (!silent) {
        popToast("ok", "Refreshed", mode === "device" ? `Connected to ${baseUrl}` : "Simulation updated");
      }
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

  const doUnlock = async (opts = {}) => {
    const suppressSuccessToast = !!opts.suppressSuccessToast;
    setBusy(true);
    try {
      await api.unlock();
      await refresh({ silent: true });
      if (!suppressSuccessToast) popToast("ok", "Unlocked", "Device reports unlocked.");
      return true;
    } catch (e) {
      popToast("err", "Unlock failed", e.message);
      return false;
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

  const addUser = async () => {
    const n = name.trim();
    if (!n) return popToast("err", "Name required", "Please enter a user name.");
    setBusy(true);
    try {
      await api.addUser(n);
      setName("");
      if (mode === "device") await refresh({ silent: true });
      popToast("ok", "Enrolled", `Added ${n}`);
    } catch (e) {
      popToast("err", "Enroll failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  /** Register user on device/sim without clearing the name field or toasting (for combined face enroll flow). */
  const addUserToDirectory = async (displayName) => {
    const n = displayName.trim();
    if (!n) throw new Error("Name required");
    await api.addUser(n);
    if (mode === "device") {
      const users = await api.users();
      setDeviceUsers(users);
    }
  };

  const delUser = async (id) => {
    if (!confirm("Remove this user and their face template (if any)?")) return;
    const list = mode === "device" ? deviceUsers : sim.users;
    const u = list.find((x) => x.id === id);
    const displayName = (u?.name ?? "").trim();
    const cleanFace = (faceApiUrl || "").trim().replace(/\/$/, "");

    setBusy(true);
    try {
      if (cleanFace && displayName) {
        try {
          const r = await fetch(`${cleanFace}/api/face/remove`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: displayName }),
          });
          if (!r.ok) {
            popToast(
              "info",
              "Face template",
              `Face API returned HTTP ${r.status}. User will still be removed from the list.`,
            );
          }
        } catch (e) {
          popToast(
            "info",
            "Face template",
            `${e.message || "Face API unreachable"} — user will still be removed from the list.`,
          );
        }
      }

      await api.delUser(id);
      if (mode === "sim" && displayName) {
        setSimFaceAccessAllowed((prev) => {
          const next = { ...prev };
          delete next[displayName];
          return next;
        });
      }
      if (mode === "device") await refresh({ silent: true });
      popToast("ok", "Removed", "User and face data updated where available.");
    } catch (e) {
      popToast("err", "Delete failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveSettings = async () => {
    setBusy(true);
    try {
      await api.saveSettings(settings);
      if (mode === "device") await refresh({ silent: true });
      popToast("ok", "Saved", "Settings updated.");
    } catch (e) {
      popToast("err", "Save failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  return {
    refresh,
    doUnlock,
    doLockSim,
    addUser,
    addUserToDirectory,
    delUser,
    saveSettings,
  };
}
