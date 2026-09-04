import { useEffect, useRef, useState } from "react";
import useApi from "./useApi";
import { genId } from "../utils/helpers";

export default function useAppState() {
  const [mode, setMode] = useState(() => localStorage.getItem("mode") || "sim");
  // Empty means same-origin: the Pi serves this app, so /api/... is a relative
  // path. That is what lets SameSite=Strict session cookies work at all -- a
  // cross-site cookie would never be sent. An explicit URL is still accepted
  // for the in-browser mock and the simulator.
  const [baseUrl, setBaseUrl] = useState(
    () => localStorage.getItem("baseUrl") ?? ""
  );
  const [faceApiUrl, setFaceApiUrl] = useState(
    () => localStorage.getItem("faceApiUrl") || "http://127.0.0.1:8765"
  );
  // Who this browser is signed in as, learned from the Pi via /api/me. Never
  // trusted for authorization -- the server re-derives identity from the
  // session cookie on every request. This only decides what the UI offers.
  const [me, setMe] = useState(null);
  const [authState, setAuthState] = useState("unknown"); // unknown | out | in
  const [tab, setTab] = useState("control");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [name, setName] = useState("");

  const [sim, setSim] = useState(() => {
    const raw = localStorage.getItem("sim");
    if (raw) {
      try {
        const saved = JSON.parse(raw);
        const defaultSettings = {
          autoRelockSeconds: 10,
          ignitionAutoStopSeconds: 20,
          promptAutoLockSeconds: 0,
          liveness: true,
          failLockout: true,
          lockoutAfter: 5,
        };
        // Always begin a new UI session in locked state for unlock-flow testing.
        return {
          ...saved,
          locked: true,
          settings: { ...defaultSettings, ...(saved.settings || {}) },
        };
      } catch {
        localStorage.removeItem("sim");
      }
    }
    return {
      deviceName: "FaceLock-Pi",
      locked: true,
      ignitionOn: false,
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
      settings: {
        autoRelockSeconds: 10,
        ignitionAutoStopSeconds: 20,
        promptAutoLockSeconds: 0,
        liveness: true,
        failLockout: true,
        lockoutAfter: 5,
      },
    };
  });

  const [status, setStatus] = useState({
    online: true,
    lockState: "locked",
    ignitionOn: false,
    deviceName: "",
    battery: 0,
    signal: 0,
    lastSeen: 0,
  });

  const [settings, setSettings] = useState(sim.settings);

  const [deviceUsers, setDeviceUsers] = useState([]);
  const [deviceLogs, setDeviceLogs] = useState([]);

  const [simFaceAccessAllowed, setSimFaceAccessAllowed] = useState(() => {
    try {
      const raw = localStorage.getItem("faceAccessAllowed");
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  });

  const toastDismissRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("mode", mode);
    localStorage.setItem("baseUrl", baseUrl);
    localStorage.setItem("faceApiUrl", faceApiUrl);
    localStorage.setItem("sim", JSON.stringify(sim));
  }, [mode, baseUrl, faceApiUrl, sim]);

  // One-time cleanup for browsers upgrading from the shared-token build. The
  // session now lives in an HttpOnly cookie, so any token left in localStorage
  // is both useless and a liability sitting in a script-readable store.
  useEffect(() => {
    try {
      localStorage.removeItem("apiToken");
    } catch {
      /* private mode or blocked storage: nothing to clean up */
    }
  }, []);

  useEffect(() => {
    localStorage.setItem("faceAccessAllowed", JSON.stringify(simFaceAccessAllowed));
  }, [simFaceAccessAllowed]);

  const faceAccessAllowed =
    mode === "device"
      ? Object.fromEntries(deviceUsers.map((u) => [u.name, u.faceAccess !== false]))
      : simFaceAccessAllowed;

  const setFaceAccessAllowed = mode === "device" ? () => {} : setSimFaceAccessAllowed;

  useEffect(
    () => () => {
      if (toastDismissRef.current) clearTimeout(toastDismissRef.current);
    },
    [],
  );

  const api = useApi(mode, baseUrl, sim, setSim);

  const popToast = (type, title, msg, durationMs = 5600) => {
    if (toastDismissRef.current) {
      clearTimeout(toastDismissRef.current);
      toastDismissRef.current = null;
    }
    setToast({ id: genId("t"), type, title, msg });
    toastDismissRef.current = setTimeout(() => {
      toastDismissRef.current = null;
      setToast(null);
    }, durationMs);
  };

  return {
    mode,
    setMode,
    baseUrl,
    setBaseUrl,
    faceApiUrl,
    setFaceApiUrl,
    me,
    setMe,
    authState,
    setAuthState,
    isAdmin: me?.role === "ADMIN",
    tab,
    setTab,
    busy,
    setBusy,
    toast,
    setToast,
    popToast,
    sim,
    setSim,
    status,
    setStatus,
    api,
    name,
    setName,
    settings,
    setSettings,
    deviceUsers,
    setDeviceUsers,
    deviceLogs,
    setDeviceLogs,
    faceAccessAllowed,
    setFaceAccessAllowed,
    setSimFaceAccessAllowed,
  };
}
