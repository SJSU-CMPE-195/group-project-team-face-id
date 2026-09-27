import { useCallback, useEffect, useRef } from "react";
import { genId } from "../utils/helpers";
import useSecurity from "../security/useSecurity.js";
import useOperationGrant from "../security/useOperationGrant.js";
import { resolveRuntimeCapabilities } from "../utils/actuatorStatus.js";

export default function useAppActions(state) {
  const { user } = useSecurity();
  const { requestGrant } = useOperationGrant();
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
    faceApiUrl,
    sim,
    deviceUsers,
    setSimFaceAccessAllowed,
  } = state;
  const simRelockTimerRef = useRef(null);
  const simIgnitionStopTimerRef = useRef(null);
  const deviceIgnitionStopCheckTimerRef = useRef(null);
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

  const refresh = async (opts = {}) => {
    const silent = !!opts.silent;
    setBusy(true);
    try {
      const [remoteStatus, deviceInfo] = await Promise.all([
        api.status(),
        api.deviceInfo(),
      ]);
      const refreshedStatus = {
        ...remoteStatus,
        capabilities: deviceInfo.capabilities,
      };
      setStatus(refreshedStatus);
      if (mode === "device") {
        const [users, logs, remoteSettings, devices] = await Promise.all([
          api.users(),
          user.is_admin ? api.logs() : Promise.resolve([]),
          api.getSettings(),
          user.is_admin ? api.devices() : Promise.resolve([]),
        ]);
        setDeviceUsers(users);
        setDeviceLogs(logs);
        setDevices(devices);
        setSettings((prev) => ({ ...prev, ...remoteSettings }));
      }
      if (!silent) {
        popToast("ok", "Refreshed", mode === "device" ? `Connected to ${baseUrl}` : "Simulation updated");
      }
      return refreshedStatus;
    } catch (e) {
      setStatus((p) => ({ ...p, online: false }));
      if (!silent) popToast("err", "Connection failed", e.message);
      return null;
    } finally {
      setBusy(false);
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

  /** Register user on device/sim without clearing the name field or toasting (for combined face enroll flow). */
  const addUserToDirectory = async (displayName, pin, isAdmin = false) => {
    const n = displayName.trim();
    if (!n) throw new Error("Name required");
    if (n.length > 100) throw new Error("Display name must be 100 characters or fewer.");
    const grant = await requestGrant("user.create", "", `create ${n}`, {
      name: n,
      pin,
    });
    if (!grant) return null;
    const createdUser = await api.addUser(n, pin, isAdmin, grant, true);
    const { enrollment_grant: _enrollmentGrant, ...user } = createdUser;
    if (mode === "device") {
      setDeviceUsers((users) => [...users, user]);
    } else {
      setSim((previous) => ({
        ...previous,
        users: [...previous.users, user],
      }));
    }
    return createdUser;
  };

  const delUser = async (id) => {
    if (!confirm("Remove this user and their face template (if any)?")) return;
    const list = mode === "device" ? deviceUsers : sim.users;
    const u = list.find((x) => x.id === id);
    const displayName = (u?.name ?? "").trim();
    const cleanFace = (faceApiUrl || "").trim().replace(/\/$/, "");

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
