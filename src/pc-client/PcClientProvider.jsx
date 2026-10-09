import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PcClientContext } from "./pcClientContext.js";

const CLIENT_ROOT = "/local/client";

function clientError(response, payload) {
  const message = payload?.error || payload?.message || payload?.detail;
  const error = new Error(
    typeof message === "string"
      ? message
      : `HTTP ${response.status} ${response.statusText}`,
  );
  error.status = response.status;
  error.code = payload?.code;
  error.payload = payload;
  return error;
}

async function parseJsonResponse(response) {
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw clientError(response, payload);
  if (!payload || typeof payload !== "object") {
    throw new Error("The PC Bluetooth service returned an invalid response.");
  }
  return payload;
}

export default function PcClientProvider({ children }) {
  const [status, setStatus] = useState(null);
  const [devices, setDevices] = useState([]);
  const [scanComplete, setScanComplete] = useState(false);
  const [stagedCard, setStagedCard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState("");
  const csrfRef = useRef("");
  const mountedRef = useRef(true);
  const statusRequestRef = useRef(0);
  const operationPendingRef = useRef(false);

  const applyStatus = useCallback((payload) => {
    if (!mountedRef.current) return payload;
    if (typeof payload?.csrf_token === "string") {
      csrfRef.current = payload.csrf_token;
    }
    setStatus(payload);
    return payload;
  }, []);

  const request = useCallback(async (path, options = {}) => {
    const response = await fetch(`${CLIENT_ROOT}${path}`, {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.csrf === false
          ? {}
          : { "X-BASS-CSRF": csrfRef.current }),
        ...(options.headers || {}),
      },
    });
    return parseJsonResponse(response);
  }, []);

  const refresh = useCallback(async () => {
    const requestId = ++statusRequestRef.current;
    const response = await fetch(`${CLIENT_ROOT}/status`, {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
    });
    const payload = await parseJsonResponse(response);
    if (requestId !== statusRequestRef.current) return payload;
    applyStatus(payload);
    if (mountedRef.current) {
      setError("");
      setErrorCode("");
    }
    return payload;
  }, [applyStatus]);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    void refresh()
      .then(() => {
        if (mountedRef.current) setError("");
      })
      .catch((requestError) => {
        if (mountedRef.current) setError(requestError.message);
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
    return () => {
      mountedRef.current = false;
      statusRequestRef.current += 1;
    };
  }, [refresh]);

  const run = useCallback(
    async (operation) => {
      if (operationPendingRef.current) {
        throw new Error("Finish the current Bluetooth request first.");
      }
      operationPendingRef.current = true;
      setBusy(true);
      setError("");
      setErrorCode("");
      try {
        return await operation();
      } catch (requestError) {
        if (mountedRef.current) {
          setError(requestError.message);
          setErrorCode(requestError.code || "");
        }
        throw requestError;
      } finally {
        operationPendingRef.current = false;
        if (mountedRef.current) setBusy(false);
      }
    },
    [],
  );

  const stageCard = useCallback(
    (card) =>
      run(async () => {
        const payload = await request("/card", {
          method: "POST",
          body: JSON.stringify({ card }),
        });
        setStagedCard(payload.card || null);
        setDevices([]);
        setScanComplete(false);
        await refresh();
        return payload;
      }),
    [refresh, request, run],
  );

  const scan = useCallback(
    () =>
      run(async () => {
        const payload = await request("/scan", {
          method: "POST",
          body: "{}",
        });
        setDevices(Array.isArray(payload.devices) ? payload.devices : []);
        setScanComplete(true);
        await refresh();
        return payload;
      }),
    [refresh, request, run],
  );

  const connect = useCallback(
    (candidateId) =>
      run(async () => {
        const payload = await request("/connect", {
          method: "POST",
          body: JSON.stringify({ candidate_id: candidateId }),
        });
        await refresh();
        return payload;
      }),
    [refresh, request, run],
  );

  const commission = useCallback(
    (input) =>
      run(async () => {
        try {
          const payload = await request("/commission", {
            method: "POST",
            body: JSON.stringify(input),
          });
          await refresh();
          return payload;
        } catch (requestError) {
          await refresh().catch(() => {});
          throw requestError;
        }
      }),
    [refresh, request, run],
  );

  const retryCommission = useCallback(
    (pin) =>
      run(async () => {
        try {
          const payload = await request("/commission/retry", {
            method: "POST",
            body: JSON.stringify({ pin }),
          });
          await refresh();
          return payload;
        } catch (requestError) {
          await refresh().catch(() => {});
          throw requestError;
        }
      }),
    [refresh, request, run],
  );

  const disconnect = useCallback(
    () =>
      run(async () => {
        const payload = await request("/disconnect", {
          method: "POST",
          body: "{}",
        });
        setDevices([]);
        setScanComplete(false);
        setStagedCard(null);
        await refresh();
        return payload;
      }),
    [refresh, request, run],
  );

  const forget = useCallback(
    (confirm) =>
      run(async () => {
        const payload = await request("/forget", {
          method: "POST",
          body: JSON.stringify({ confirm }),
        });
        setDevices([]);
        setScanComplete(false);
        setStagedCard(null);
        await refresh();
        return payload;
      }),
    [refresh, request, run],
  );

  const downloadRecoveryCard = useCallback(
    (pin) =>
      run(async () => {
        const response = await fetch(`${CLIENT_ROOT}/recovery-card`, {
          method: "POST",
          cache: "no-store",
          credentials: "same-origin",
          redirect: "error",
          headers: {
            "Content-Type": "application/json",
            "X-BASS-CSRF": csrfRef.current,
          },
          body: JSON.stringify({ pin }),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => null);
          throw clientError(response, payload);
        }
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
          throw new Error("The PC Bluetooth service returned an invalid recovery file.");
        }
        return {
          blob: await response.blob(),
          disposition: response.headers.get("content-disposition") || "",
        };
      }),
    [run],
  );

  const acknowledgeRecoveryCard = useCallback(
    () =>
      run(async () => {
        const payload = await request("/recovery-card/acknowledge", {
          method: "POST",
          body: "{}",
        });
        await refresh();
        return payload;
      }),
    [refresh, request, run],
  );

  const clearError = useCallback(() => {
    setError("");
    setErrorCode("");
  }, []);

  const value = useMemo(
    () => ({
      status,
      devices,
      scanComplete,
      stagedCard,
      loading,
      busy,
      error,
      errorCode,
      refresh,
      stageCard,
      scan,
      connect,
      commission,
      retryCommission,
      disconnect,
      forget,
      downloadRecoveryCard,
      acknowledgeRecoveryCard,
      clearError,
    }),
    [
      acknowledgeRecoveryCard,
      busy,
      clearError,
      commission,
      connect,
      devices,
      disconnect,
      downloadRecoveryCard,
      error,
      errorCode,
      forget,
      loading,
      refresh,
      retryCommission,
      scan,
      scanComplete,
      stageCard,
      stagedCard,
      status,
    ],
  );

  return (
    <PcClientContext.Provider value={value}>
      {children}
    </PcClientContext.Provider>
  );
}
