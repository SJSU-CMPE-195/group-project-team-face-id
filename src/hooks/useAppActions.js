import { useCallback, useEffect, useRef } from "react";
import { genId } from "../utils/helpers";
import useSecurity from "../security/useSecurity.js";
import useOperationGrant from "../security/useOperationGrant.js";
import usePcClient from "../pc-client/usePcClient.js";
import { resolveRuntimeCapabilities } from "../utils/actuatorStatus.js";

export default function useAppActions(state) {
  const { user, syncUser, expireSession } = useSecurity();
  const { requestGrant } = useOperationGrant();
  const pcClient = usePcClient();
  const {
    mode,
    baseUrl,
    setBusy,
    popToast,
    setSim,
    setStatus,
    api,
    status,
    settings,
    setSettings,
    setDeviceUsers,
    setDeviceLogs,
    setDevices,
    sim,
    deviceUsers,
    setSimFaceAccessAllowed,
  } = state;
  const simRelockTimerRef = useRef(null);
  const simIgnitionStopTimerRef = useRef(null);
  const deviceIgnitionStopCheckTimerRef = useRef(null);
  const refreshRequestRef = useRef(0);
  const refreshRef = useRef(null);
  const interactiveRefreshRef = useRef(false);
  const pcRefreshActiveRef = useRef(null);
  const pcRefreshPendingRef = useRef(null);
  const { simulatedActuators } = resolveRuntimeCapabilities({
    mode,
    runtime: status?.runtime,
    capabilities: status?.capabilities,
  });

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

  const performRefresh = async (opts = {}) => {
    const silent = !!opts.silent;
    if (!pcClient && silent && interactiveRefreshRef.current) return null;
    const request = refreshRequestRef.current + 1;
    refreshRequestRef.current = request;
    if (!silent) {
      interactiveRefreshRef.current = true;
      setBusy(true);
    }
    try {
      const [remoteStatus, deviceInfo, identity] = await Promise.all([
        api.status(),
        api.deviceInfo(),
        api.me(),
      ]);
      if (request !== refreshRequestRef.current) return null;
      const refreshedStatus = {
        ...remoteStatus,
        capabilities: {
          ...(deviceInfo.capabilities || {}),
          ...(remoteStatus.capabilities || {}),
        },
      };
      setStatus(refreshedStatus);
      if (mode === "device") {
        const refreshedUser = identity.user;
        if (!refreshedUser?.id || refreshedUser.id !== user.id) {
          setDeviceUsers([]);
          setDeviceLogs([]);
          setDevices([]);
          expireSession("login");
          return null;
        }
        const isAdmin = !!refreshedUser?.is_admin;
        const localControls =
          refreshedStatus.capabilities.local_controls === true;
        const identityChanged =
          isAdmin !== !!user.is_admin ||
          !!refreshedUser?.is_owner !== !!user.is_owner;
        if (identityChanged) {
          setDeviceUsers([]);
          setDeviceLogs([]);
          setDevices([]);
        }
        const [users, logs, remoteSettings, devices] = await Promise.all([
          api.users(),
          api.logs(),
          localControls && isAdmin ? api.getSettings() : Promise.resolve(null),
          isAdmin ? api.devices() : Promise.resolve([]),
        ]);
        if (request !== refreshRequestRef.current) return null;
        setDeviceUsers(users);
        setDeviceLogs(logs);
        setDevices(devices);
        if (remoteSettings) {
          setSettings((prev) => ({ ...prev, ...remoteSettings }));
        }
        syncUser(refreshedUser);
      }
      if (!silent) {
        popToast("ok", "Refreshed", mode === "device" ? `Connected to ${baseUrl}` : "Simulation updated");
      }
      return refreshedStatus;
    } catch (e) {
      if (request !== refreshRequestRef.current) return null;
      if (e.status === 401 || e.status === 403) {
        setDeviceUsers([]);
        setDeviceLogs([]);
        setDevices([]);
        if (e.status === 403) {
          try {
            const identity = await api.me();
            if (request !== refreshRequestRef.current) return null;
            if (!identity.user?.id || identity.user.id !== user.id) {
              expireSession("login");
            } else {
              syncUser(identity.user);
            }
          } catch (identityError) {
            if (identityError.status === 401) expireSession("login");
          }
        }
        if (!silent) popToast("err", "Access changed", "Your current permissions were refreshed.");
        return null;
      }
      setStatus((p) => ({ ...p, online: false }));
      if (!silent) popToast("err", "Connection failed", e.message);
      return null;
    } finally {
      if (!silent && request === refreshRequestRef.current) {
        interactiveRefreshRef.current = false;
        setBusy(false);
      }
    }
  };

  const startPcRefresh = (opts) => {
    const promise = performRefresh(opts);
    pcRefreshActiveRef.current = promise;
    const drain = () => {
      if (pcRefreshActiveRef.current !== promise) return;
      pcRefreshActiveRef.current = null;
      const pending = pcRefreshPendingRef.current;
      pcRefreshPendingRef.current = null;
      if (!pending) return;
      if (!refreshRef.current) {
        pending.waiters.forEach(({ resolve }) => resolve(null));
        return;
      }
      const next = refreshRef.current(pending.options);
      next.then(
        (value) => pending.waiters.forEach(({ resolve }) => resolve(value)),
        (error) => pending.waiters.forEach(({ reject }) => reject(error)),
      );
    };
    promise.then(drain, drain);
    return promise;
  };

  const refresh = (opts = {}) => {
    if (!pcClient) return performRefresh(opts);
    if (!pcRefreshActiveRef.current) return startPcRefresh(opts);
    return new Promise((resolve, reject) => {
      const silent = !!opts.silent;
      if (pcRefreshPendingRef.current) {
        pcRefreshPendingRef.current.options.silent =
          pcRefreshPendingRef.current.options.silent && silent;
        pcRefreshPendingRef.current.waiters.push({ resolve, reject });
        return;
      }
      pcRefreshPendingRef.current = {
        options: { ...opts, silent },
        waiters: [{ resolve, reject }],
      };
    });
  };
  refreshRef.current = refresh;

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

  useEffect(() => {
    if (mode !== "device") return undefined;
    const refreshVisibleSession = () => {
      if (document.visibilityState === "visible") {
        void refreshRef.current?.({ silent: true });
      }
    };
    const interval = window.setInterval(refreshVisibleSession, 15_000);
    window.addEventListener("focus", refreshVisibleSession);
    document.addEventListener("visibilitychange", refreshVisibleSession);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refreshVisibleSession);
      document.removeEventListener("visibilitychange", refreshVisibleSession);
    };
  }, [mode]);

  useEffect(
    () => () => {
      refreshRequestRef.current += 1;
      refreshRef.current = null;
      interactiveRefreshRef.current = false;
      pcRefreshActiveRef.current = null;
      const pendingRefresh = pcRefreshPendingRef.current;
      pcRefreshPendingRef.current = null;
      pendingRefresh?.waiters.forEach(({ resolve }) => resolve(null));
      clearSimRelockTimer();
      clearSimIgnitionStopTimer();
      clearDeviceIgnitionStopCheckTimer();
    },
    [clearDeviceIgnitionStopCheckTimer, clearSimIgnitionStopTimer, clearSimRelockTimer],
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

  const doLock = async () => {
    if (mode === "device" && status?.capabilities?.local_controls !== true) {
      popToast("err", "Local control required", "Open the dashboard on the trusted host computer.");
      return false;
    }
    setBusy(true);
    try {
      if (mode === "sim") clearSimRelockTimer();
      if (mode === "sim") clearSimIgnitionStopTimer();
      if (mode === "device") {
        clearDeviceIgnitionStopCheckTimer();
      }
      let result = null;
      if (mode === "sim") {
        setSim((s) => ({
          ...s,
          locked: true,
          ignitionOn: false,
          logs: [{ id: genId("log"), ts: Date.now(), type: "lock", ok: true, detail: "Locked (sim)" }, ...s.logs].slice(0, 80),
        }));
      } else {
        const grant = await requestGrant("device.lock", "", "lock device");
        if (!grant) return false;
        result = await api.lock(grant);
      }
      const refreshed = await refresh({ silent: true });
      popToast(
        refreshed ? "ok" : "info",
        simulatedActuators && refreshed
          ? "Simulation updated"
          : "Lock command accepted",
        !refreshed
          ? "Command accepted, but updated device state could not be refreshed."
          : simulatedActuators
            ? "Simulated lock state is locked."
            : result?.physical_state_confirmed === true
            ? "Physical lock state is confirmed."
              : "Physical lock position remains unconfirmed.",
      );
      return true;
    } catch (e) {
      if (e.payload?.command_sent === true && e.payload?.locked === true) {
        clearSimRelockTimer();
        clearSimIgnitionStopTimer();
        clearDeviceIgnitionStopCheckTimer();
        const refreshed = await refresh({ silent: true });
        popToast(
          "info",
          "Lock commands sent",
          `The device accepted stop and lock, but ${e.message}.${
            refreshed ? "" : " Updated device state could not be refreshed."
          }`,
        );
        return true;
      }
      popToast("err", "Lock failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const doIgnitionStop = async () => {
    if (mode === "device" && status?.capabilities?.local_controls !== true) {
      popToast("err", "Local control required", "Open the dashboard on the trusted host computer.");
      return false;
    }
    setBusy(true);
    try {
      const grant = await requestGrant(
        "ignition.stop",
        "",
        "stop ignition",
      );
      if (!grant) return false;
      const result = await api.ignitionStop(grant);
      if (mode === "sim") clearSimIgnitionStopTimer();
      if (mode === "device") clearDeviceIgnitionStopCheckTimer();
      const refreshed = await refresh({ silent: true });
      popToast(
        refreshed ? "ok" : "info",
        simulatedActuators && refreshed
          ? "Simulation updated"
          : "Stop command accepted",
        !refreshed
          ? "Command accepted, but updated device state could not be refreshed."
          : simulatedActuators
            ? "Simulated ignition is stopped."
            : result?.physical_state_confirmed === true
            ? "Physical ignition stop is confirmed."
              : "Physical ignition state remains unconfirmed.",
      );
      return true;
    } catch (e) {
      popToast("err", "Ignition stop failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const doFullReset = async () => {
    if (mode === "device" && status?.capabilities?.local_controls !== true) {
      popToast("err", "Local control required", "Open the dashboard on the trusted host computer.");
      return false;
    }
    setBusy(true);
    try {
      const grant = await requestGrant("device.reset", "", "full reset");
      if (!grant) return false;
      const result = await api.fullReset(grant);
      clearSimRelockTimer();
      clearSimIgnitionStopTimer();
      clearDeviceIgnitionStopCheckTimer();
      const refreshed = await refresh({ silent: true });
      popToast(
        refreshed ? "ok" : "info",
        simulatedActuators && refreshed
          ? "Simulation reset"
          : "Reset command accepted",
        !refreshed
          ? "Reset accepted, but updated device state could not be refreshed."
          : simulatedActuators
            ? "Simulated ignition stopped and lock engaged."
            : result?.physical_state_confirmed === true
            ? "Physical stop and lock state are confirmed."
              : "Physical lock and ignition states remain unconfirmed.",
      );
      return true;
    } catch (e) {
      if (e.payload?.command_sent === true && e.payload?.locked === true) {
        clearSimRelockTimer();
        clearSimIgnitionStopTimer();
        clearDeviceIgnitionStopCheckTimer();
        const refreshed = await refresh({ silent: true });
        popToast(
          "info",
          "Reset partially completed",
          `The device accepted stop and lock, but ${e.message}.${
            refreshed ? "" : " Updated device state could not be refreshed."
          }`,
        );
        return false;
      }
      popToast("err", "Full reset failed", e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const addUserToDirectory = async (displayName, pin) => {
    const n = displayName.trim();
    if (!n) throw new Error("Name required");
    if (n.length > 100) throw new Error("Display name must be 100 characters or fewer.");
    const grant = await requestGrant("user.create", "", `create ${n}`, {
      name: n,
      pin,
    });
    if (!grant) return null;
    const result = await api.addUser(n, pin, grant);
    const createdUser = result.user || result;
    if (mode === "device") {
      setDeviceUsers((users) => [...users, createdUser]);
    } else {
      setSim((previous) => ({
        ...previous,
        users: [...previous.users, createdUser],
      }));
    }
    return createdUser;
  };

  const delUser = async (id) => {
    if (!confirm("Remove this user and their face template (if any)?")) return;
    const list = mode === "device" ? deviceUsers : sim.users;
    const u = list.find((x) => x.id === id);
    const displayName = (u?.name ?? "").trim();

    setBusy(true);
    try {
      const grant = await requestGrant("user.delete", id, "remove user");
      if (!grant) return;
      await api.delUser(id, grant);
      if (mode === "device") {
        setDeviceUsers((users) => users.filter((user) => user.id !== id));
      } else {
        setSim((previous) => ({
          ...previous,
          users: previous.users.filter((user) => user.id !== id),
        }));
      }

      if (mode === "sim" && displayName) {
        setSimFaceAccessAllowed((prev) => {
          const next = { ...prev };
          delete next[displayName];
          return next;
        });
      }
      const refreshed =
        mode === "device" ? await refresh({ silent: true }) : true;
      popToast(
        refreshed ? "ok" : "info",
        "Removed",
        refreshed
          ? "User and face data updated where available."
          : "User removed, but the latest device data could not be refreshed.",
      );
    } catch (e) {
      popToast("err", "Delete failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveSettings = async () => {
    if (mode === "device" && status?.capabilities?.local_controls !== true) {
      popToast("err", "Local settings required", "Open the dashboard on the trusted host computer.");
      return;
    }
    setBusy(true);
    try {
      const grant = await requestGrant(
        "settings.update",
        "",
        "save settings",
      );
      if (!grant) return;
      await api.saveSettings(settings, grant);
      const refreshed =
        mode === "device" ? await refresh({ silent: true }) : true;
      popToast(
        refreshed ? "ok" : "info",
        "Saved",
        refreshed
          ? "Settings updated."
          : "Settings saved, but the latest values could not be refreshed.",
      );
    } catch (e) {
      popToast("err", "Save failed", e.message);
    } finally {
      setBusy(false);
    }
  };

  return {
    refresh,
    doLock,
    doIgnitionStop,
    doFullReset,
    addUserToDirectory,
    delUser,
    saveSettings,
  };
}
