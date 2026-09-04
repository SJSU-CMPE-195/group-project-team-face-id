import { useCallback, useEffect, useRef } from "react";
import { genId } from "../utils/helpers";
import { AuthError } from "./useApi";

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
    setMe,
    setAuthState,
    isAdmin,
  } = state;
  const simRelockTimerRef = useRef(null);
  const simIgnitionStopTimerRef = useRef(null);
  const deviceRelockCheckTimerRef = useRef(null);
  const deviceIgnitionStopCheckTimerRef = useRef(null);

  const clearSimRelockTimer = useCallback(() => {
    if (simRelockTimerRef.current) {
      clearTimeout(simRelockTimerRef.current);
      simRelockTimerRef.current = null;
    }
  }, []);

  const clearSimIgnitionStopTimer = useCallback(() => {
    if (simIgnitionStopTimerRef.current) {
      clearTimeout(simIgnitionStopTimerRef.current);
      simIgnitionStopTimerRef.current = null;
    }
  }, []);

  const scheduleSimIgnitionStop = useCallback(() => {
    clearSimIgnitionStopTimer();
    if (mode !== "sim") return;
    const secs = Math.max(0, Number(settings?.ignitionAutoStopSeconds) || 0);
    if (secs <= 0) return;
    simIgnitionStopTimerRef.current = setTimeout(() => {
      simIgnitionStopTimerRef.current = null;
      setSim((s) => {
        if (!s.ignitionOn) return s;
        return {
          ...s,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "ignition", ok: true, detail: `timeout_${secs}s` }, ...s.logs].slice(0, 80),
        };
      });
      popToast("info", "Ignition auto-stop", `Stopped after ${secs}s.`);
    }, secs * 1000);
  }, [clearSimIgnitionStopTimer, mode, popToast, setSim, settings?.ignitionAutoStopSeconds]);

  const clearDeviceRelockCheckTimer = useCallback(() => {
    if (deviceRelockCheckTimerRef.current) {
      clearTimeout(deviceRelockCheckTimerRef.current);
      deviceRelockCheckTimerRef.current = null;
    }
  }, []);

  const clearDeviceIgnitionStopCheckTimer = useCallback(() => {
    if (deviceIgnitionStopCheckTimerRef.current) {
      clearTimeout(deviceIgnitionStopCheckTimerRef.current);
      deviceIgnitionStopCheckTimerRef.current = null;
    }
  }, []);

  const scheduleSimRelock = useCallback(() => {
    clearSimRelockTimer();
    if (mode !== "sim") return;
    const secs = Math.max(0, Number(settings?.autoRelockSeconds) || 0);
    if (secs <= 0) return;
    simRelockTimerRef.current = setTimeout(() => {
      simRelockTimerRef.current = null;
      setSim((s) => {
        if (s.locked) return s;
        return {
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [
            { id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: `auto_relock_${secs}s` },
            ...s.logs,
          ].slice(0, 80),
        };
      });
      popToast("info", "Auto re-lock", `Locked after ${secs}s.`);
    }, secs * 1000);
  }, [clearSimRelockTimer, mode, popToast, setSim, settings?.autoRelockSeconds]);

  const refresh = async (opts = {}) => {
    const silent = !!opts.silent;
    setBusy(true);
    try {
      // Reachability is decided by /api/status alone. The admin reads below are
      // allowed to fail with 403 for an ordinary driver -- that is a permission
      // outcome, not the device being offline, and it must not blank the UI.
      const s = await api.status();
      setStatus(s);

      // Only an administrator may read these, so a driver does not ask at all.
      // allSettled is still used because the session can expire mid-refresh.
      if (mode === "device" && isAdmin) {
        const [users, logs, remoteSettings] = await Promise.allSettled([
          api.users(),
          api.logs(),
          api.getSettings(),
        ]);
        if (users.status === "fulfilled") setDeviceUsers(users.value);
        if (logs.status === "fulfilled") setDeviceLogs(logs.value);
        if (remoteSettings.status === "fulfilled") {
          setSettings((prev) => ({ ...prev, ...remoteSettings.value }));
        }
        // A 401 anywhere means the session is gone; send the user back to pairing.
        const expired = [users, logs, remoteSettings].some(
          (r) => r.status === "rejected" && r.reason instanceof AuthError && r.reason.status === 401,
        );
        if (expired) {
          setMe(null);
          setAuthState("out");
          return;
        }
      }
      if (!silent) {
        popToast(
          "ok",
          "Refreshed",
          mode === "device" ? "Connected to the device" : "Simulation updated",
        );
      }
    } catch (e) {
      if (e instanceof AuthError && e.status === 401) {
        setMe(null);
        setAuthState("out");
        return;
      }
      setStatus((p) => ({ ...p, online: false }));
      popToast("err", "Connection failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  // ── Session lifecycle ──────────────────────────────────────────────────

  const loadMe = useCallback(async () => {
    if (mode !== "device") {
      setAuthState("in");
      return null;
    }
    try {
      const profile = await api.me();
      setMe(profile);
      setAuthState("in");
      return profile;
    } catch (e) {
      if (e instanceof AuthError) {
        setMe(null);
        setAuthState("out");
        return null;
      }
      // A network failure is not the same as being signed out; leave the
      // session alone so a flaky connection does not force re-pairing.
      setAuthState("unknown");
      return null;
    }
  }, [api, mode, setAuthState, setMe]);

  const pairDevice = async (code) => {
    const trimmed = (code || "").trim();
    if (!trimmed) {
      popToast("err", "Pairing", "Enter the code shown on the Pi.");
      return false;
    }
    setBusy(true);
    try {
      const profile = await api.pairRedeem(trimmed);
      setMe(profile);
      setAuthState("in");
      popToast("ok", "Paired", `Signed in as ${profile.name}.`);
      await refresh({ silent: true });
      return true;
    } catch (e) {
      popToast("err", "Pairing failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      await api.logout();
    } catch {
      /* Signing out locally matters even if the server call fails. */
    } finally {
      setMe(null);
      setAuthState("out");
      setDeviceUsers([]);
      setDeviceLogs([]);
      setBusy(false);
      popToast("ok", "Signed out", "This device is no longer paired.");
    }
  };

  useEffect(() => {
    if (mode === "device" && !(baseUrl || "").trim()) {
      setStatus((p) => ({ ...p, online: false }));
      setDeviceUsers([]);
      setDeviceLogs([]);
      return;
    }
    if (mode === "device") {
      setStatus((p) => ({ ...p, online: false }));
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, baseUrl]);

  useEffect(
    () => () => {
      clearSimRelockTimer();
      clearSimIgnitionStopTimer();
      clearDeviceRelockCheckTimer();
      clearDeviceIgnitionStopCheckTimer();
    },
    [clearDeviceIgnitionStopCheckTimer, clearSimIgnitionStopTimer, clearSimRelockTimer, clearDeviceRelockCheckTimer],
  );

  useEffect(() => {
    if (mode !== "sim") {
      clearSimRelockTimer();
      return;
    }
    if (sim.locked) {
      clearSimRelockTimer();
      return;
    }
    scheduleSimRelock();
  }, [mode, sim.locked, settings?.autoRelockSeconds, scheduleSimRelock, clearSimRelockTimer]);

  useEffect(() => {
    if (mode !== "sim") {
      clearSimIgnitionStopTimer();
      return;
    }
    if (!sim.ignitionOn) {
      clearSimIgnitionStopTimer();
      return;
    }
    scheduleSimIgnitionStop();
  }, [clearSimIgnitionStopTimer, mode, scheduleSimIgnitionStop, settings?.ignitionAutoStopSeconds, sim.ignitionOn]);

  const doUnlock = async (opts = {}) => {
    const suppressSuccessToast = !!opts.suppressSuccessToast;
    setBusy(true);
    try {
      await api.unlock();
      await refresh({ silent: true });
      if (mode === "sim") scheduleSimRelock();
      if (mode === "device") {
        clearDeviceRelockCheckTimer();
        clearDeviceIgnitionStopCheckTimer();
        const secs = Math.max(0, Number(settings?.autoRelockSeconds) || 0);
        if (secs > 0) {
          // Device auto re-lock happens on backend; re-fetch status after expected timeout.
          deviceRelockCheckTimerRef.current = setTimeout(() => {
            deviceRelockCheckTimerRef.current = null;
            void refresh({ silent: true });
          }, secs * 1000 + 1200);
        }
      }
      if (!suppressSuccessToast) popToast("ok", "Unlocked", "Device reports unlocked.");
      return true;
    } catch (e) {
      popToast("err", "Unlock failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const doLock = async () => {
    setBusy(true);
    try {
      if (mode === "sim") clearSimRelockTimer();
      if (mode === "sim") clearSimIgnitionStopTimer();
      if (mode === "device") {
        clearDeviceRelockCheckTimer();
        clearDeviceIgnitionStopCheckTimer();
      }
      if (mode === "sim") {
        setSim((s) => ({
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "Locked (sim)" }, ...s.logs].slice(0, 80),
        }));
      } else {
        await api.lock();
      }
      await refresh({ silent: true });
      popToast("ok", "Locked", "Device reports locked.");
      return true;
    } catch (e) {
      popToast("err", "Lock failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
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

  const doIgnitionStop = async () => {
    setBusy(true);
    try {
      await api.ignitionStop();
      if (mode === "sim") clearSimIgnitionStopTimer();
      if (mode === "device") clearDeviceIgnitionStopCheckTimer();
      await refresh({ silent: true });
      popToast("ok", "Ignition", "Ignition stopped.");
      return true;
    } catch (e) {
      popToast("err", "Ignition stop failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const doFullReset = async () => {
    setBusy(true);
    try {
      clearSimRelockTimer();
      clearSimIgnitionStopTimer();
      clearDeviceRelockCheckTimer();
      clearDeviceIgnitionStopCheckTimer();
      await api.fullReset();
      await refresh({ silent: true });
      popToast("ok", "Full reset", "Ignition stopped and lock engaged.");
      return true;
    } catch (e) {
      popToast("err", "Full reset failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** Register user on device/sim without clearing the name field or toasting (for combined face enroll flow). */
  const addUserToDirectory = async (displayName) => {
    const n = displayName.trim();
    if (!n) throw new Error("Name required");
    const user = await api.addUser(n);
    if (mode === "device") {
      const users = await api.users();
      setDeviceUsers(users);
    }
    return user;
  };

  const delUser = async (id) => {
    if (!confirm("Remove this user and their face template (if any)?")) return;
    const list = mode === "device" ? deviceUsers : sim.users;
    const u = list.find((x) => x.id === id);
    const displayName = (u?.name ?? "").trim();
    const cleanFace = (faceApiUrl || "").trim().replace(/\/$/, "");

    setBusy(true);
    try {
      // In device mode the template is cleared through the authenticated Device
      // API. This used to POST a bare name to the Face API, which required no
      // credential at all -- anyone able to reach that port could wipe any
      // driver's face. Sim mode still talks to the local Face API directly
      // because there is no Pi in that path.
      if (mode === "sim" && cleanFace && displayName) {
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

      // delUser already clears the stored template server-side; this is the
      // explicit path for removing a face while keeping the account.
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
    loadMe,
    pairDevice,
    signOut,
    doUnlock,
    doLock,
    doIgnitionStop,
    doFullReset,
    addUser,
    addUserToDirectory,
    delUser,
    saveSettings,
  };
}
