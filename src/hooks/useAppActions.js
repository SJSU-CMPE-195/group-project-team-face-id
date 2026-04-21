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

  const delUser = async (id) => {
    if (!confirm("Remove this user?")) return;
    setBusy(true);
    try {
      await api.delUser(id);
      if (mode === "device") await refresh({ silent: true });
      popToast("ok", "Removed", "User deleted.");
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
    delUser,
    saveSettings,
  };
}
