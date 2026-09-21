import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { HardwareSimulatorContext } from "./hardwareSimulatorContext.js";

const HARDWARE_ROOT = "/local/hardware";

async function hardwareRequest(path, options = {}) {
  const response = await fetch(`${HARDWARE_ROOT}${path}`, {
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;
  if (!response.ok) {
    const detail = payload?.message || payload?.error || payload?.detail;
    const error = new Error(
      typeof detail === "string"
        ? detail
        : `HTTP ${response.status} ${response.statusText}`,
    );
    error.status = response.status;
    error.code = payload?.code;
    error.payload = payload;
    throw error;
  }
  if (!contentType.includes("application/json")) {
    throw new Error(`Expected JSON response from ${HARDWARE_ROOT}${path}`);
  }
  return payload;
}

export default function HardwareSimulatorProvider({ children }) {
  const [availability, setAvailability] = useState("loading");
  const [authenticated, setAuthenticated] = useState(false);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const statusRequestRef = useRef(null);
  const csrfTokenRef = useRef("");
  const statusValueRef = useRef(null);
  const revisionRef = useRef(0);
  const appliedRevisionRef = useRef(0);
  const mutationPendingRef = useRef(0);

  const acceptStatus = useCallback((payload, revision) => {
    if (revision < appliedRevisionRef.current) return false;
    const current = payload?.status || payload;
    const isAuthenticated = current?.authenticated === true;
    appliedRevisionRef.current = revision;
    setAvailability(current?.enabled === true ? "available" : "disabled");
    setAuthenticated(isAuthenticated);
    setStatus(isAuthenticated ? current : null);
    statusValueRef.current = isAuthenticated ? current : null;
    csrfTokenRef.current = isAuthenticated ? current.csrf_token || "" : "";
    setError("");
    return true;
  }, []);

  const applySnapshot = useCallback((snapshot, revision) => {
    if (revision < appliedRevisionRef.current) return;
    appliedRevisionRef.current = revision;
    const previous = statusValueRef.current;
    if (!previous) return;
    const next = { ...previous, ...snapshot };
    statusValueRef.current = next;
    setStatus(next);
  }, []);

  const refresh = useCallback(async () => {
    if (statusRequestRef.current || mutationPendingRef.current) return null;
    const request = new AbortController();
    const revision = revisionRef.current + 1;
    revisionRef.current = revision;
    statusRequestRef.current = request;
    try {
      const payload = await hardwareRequest("/status", {
        signal: request.signal,
      });
      acceptStatus(payload, revision);
      return payload;
    } catch (requestError) {
      if (requestError.name === "AbortError") return null;
      if (requestError.status === 404) {
        setAvailability("disabled");
        setAuthenticated(false);
        setStatus(null);
        statusValueRef.current = null;
        csrfTokenRef.current = "";
        setError("");
        return null;
      }
      if (requestError.status === 401) {
        setAvailability("available");
        setAuthenticated(false);
        setStatus(null);
        statusValueRef.current = null;
        csrfTokenRef.current = "";
        return null;
      }
      setError(
        requestError.message || "Could not reach the hardware simulator.",
      );
      throw requestError;
    } finally {
      if (statusRequestRef.current === request) statusRequestRef.current = null;
    }
  }, [acceptStatus]);

  useEffect(() => {
    void refresh().catch(() => {});
    return () => {
      const request = statusRequestRef.current;
      request?.abort();
      if (statusRequestRef.current === request) statusRequestRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (availability !== "available" || !authenticated) return undefined;
    const timer = window.setInterval(() => {
      void refresh().catch(() => {});
    }, 1000);
    return () => window.clearInterval(timer);
  }, [authenticated, availability, refresh]);

  const mutate = useCallback(async (path, body) => {
    const token = csrfTokenRef.current;
    if (!token) throw new Error("Hardware controls are not ready. Refresh and try again.");
    const revision = revisionRef.current + 1;
    revisionRef.current = revision;
    mutationPendingRef.current += 1;
    let payload = null;
    let failure = null;
    try {
      payload = await hardwareRequest(path, {
        method: "POST",
        headers: { "X-BASS-Dev-CSRF": token },
        body: JSON.stringify(body),
      });
      const snapshot = payload?.status ||
        (typeof payload?.powered === "boolean" ? payload : null);
      if (snapshot) applySnapshot(snapshot, revision);
    } catch (requestError) {
      if (requestError.status === 401 || requestError.status === 403) {
        setAuthenticated(false);
        setStatus(null);
        statusValueRef.current = null;
        csrfTokenRef.current = "";
      }
      failure = requestError;
    } finally {
      mutationPendingRef.current = Math.max(
        0,
        mutationPendingRef.current - 1,
      );
    }
    if (failure) {
      const activeStatusRequest = statusRequestRef.current;
      activeStatusRequest?.abort();
      if (statusRequestRef.current === activeStatusRequest) {
        statusRequestRef.current = null;
      }
      await refresh().catch(() => {});
      throw failure;
    }
    return payload;
  }, [applySnapshot, refresh]);

  const readCard = useCallback(async () => {
    const token = csrfTokenRef.current;
    if (!token) throw new Error("Hardware controls are not ready. Refresh and try again.");
    const generation = statusValueRef.current?.generation;
    const payload = await hardwareRequest("/card", {
      headers: { "X-BASS-Dev-CSRF": token },
    });
    if (generation !== statusValueRef.current?.generation) {
      throw new Error("Device state changed while loading the activation card. Try again.");
    }
    return payload;
  }, []);

  const buttonDown = useCallback(
    (pressId) => mutate("/button/down", { press_id: pressId }),
    [mutate],
  );
  const buttonKeepalive = useCallback(
    (pressId) => mutate("/button/keepalive", { press_id: pressId }),
    [mutate],
  );
  const buttonUp = useCallback(
    (pressId) => mutate("/button/up", { press_id: pressId }),
    [mutate],
  );
  const buttonCancel = useCallback(
    (pressId) => mutate("/button/cancel", { press_id: pressId }),
    [mutate],
  );
  const setPower = useCallback(
    (powered) => mutate("/power", { powered }),
    [mutate],
  );
  const reset = useCallback(
    (requestId) =>
      mutate("/reset", { request_id: requestId, confirmation: "RESET" }),
    [mutate],
  );

  const value = useMemo(
    () => ({
      availability,
      enabled: availability === "available",
      authenticated,
      status,
      error,
      refresh,
      readCard,
      buttonDown,
      buttonKeepalive,
      buttonUp,
      buttonCancel,
      setPower,
      reset,
    }),
    [
      authenticated,
      availability,
      buttonCancel,
      buttonDown,
      buttonKeepalive,
      buttonUp,
      error,
      readCard,
      refresh,
      reset,
      setPower,
      status,
    ],
  );

  return (
    <HardwareSimulatorContext.Provider value={value}>
      {children}
    </HardwareSimulatorContext.Provider>
  );
}
