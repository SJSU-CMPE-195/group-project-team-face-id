import { useEffect, useState } from "react";
import useApi from "./useApi";
import { genId } from "../utils/helpers";

export default function useAppState() {
  const [mode, setMode] = useState(() => localStorage.getItem("mode") || "sim");
  const [baseUrl, setBaseUrl] = useState(
    () => localStorage.getItem("baseUrl") || "http://192.168.4.1:5000"
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

  useEffect(() => {
    localStorage.setItem("mode", mode);
    localStorage.setItem("baseUrl", baseUrl);
    localStorage.setItem("sim", JSON.stringify(sim));
  }, [mode, baseUrl, sim]);

  const api = useApi(mode, baseUrl, sim, setSim);

  const popToast = (type, title, msg) => {
    setToast({ id: genId("t"), type, title, msg });
    setTimeout(() => setToast(null), 2500);
  };

  return {
    mode,
    setMode,
    baseUrl,
    setBaseUrl,
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
  };
}
