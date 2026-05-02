import { useEffect, useRef, useState } from "react";
import useApi from "./useApi";
import { genId } from "../utils/helpers";

export default function useAppState() {
  const [mode, setMode] = useState(() => localStorage.getItem("mode") || "sim");
  const [baseUrl, setBaseUrl] = useState(
    () => localStorage.getItem("baseUrl") || "http://192.168.4.1:5000"
  );
  const [faceApiUrl, setFaceApiUrl] = useState(
    () => localStorage.getItem("faceApiUrl") || "http://127.0.0.1:8765"
  );
  const [tab, setTab] = useState("control");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [name, setName] = useState("");

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

  const [status, setStatus] = useState({
    online: true,
    lockState: "locked",
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
