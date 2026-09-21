import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Btn from "../components/Btn.jsx";
import Input from "../components/Input.jsx";
import { OperationGrantContext } from "./operationGrantContext.js";
import useSecurity from "./useSecurity.js";

const API_ROOT = "/local/wireless/api";

export default function OperationGrantProvider({ children }) {
  const { csrfToken, expireSession } = useSecurity();
  const pendingRef = useRef(null);
  const [prompt, setPrompt] = useState(null);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const finish = useCallback((value) => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    setPrompt(null);
    setPin("");
    setError("");
    setBusy(false);
    pending?.resolve(value);
  }, []);

  useEffect(() => () => pendingRef.current?.resolve(null), []);

  const requestGrant = useCallback((action, targetUserId = "", label = "this operation") => {
    if (pendingRef.current) {
      return Promise.reject(new Error("Finish the current PIN request first."));
    }
    setPrompt({ action, targetUserId: targetUserId || "", label });
    return new Promise((resolve) => {
      pendingRef.current = { resolve };
    });
  }, []);

  async function submit(event) {
    event.preventDefault();
    if (!prompt || !pendingRef.current) return;
    if (!/^\d{6}$/.test(pin)) {
      setError("PIN must be exactly 6 ASCII digits.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_ROOT}/operation-grants`, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        headers: {
          "Content-Type": "application/json",
          "X-BASS-CSRF": csrfToken,
        },
        body: JSON.stringify({
          pin,
          action: prompt.action,
          target_user_id: prompt.targetUserId,
        }),
      });
      const payload = await response.json().catch(() => null);
      if (
        response.status === 401 &&
        ["login_required", "invalid_device_credential"].includes(payload?.code)
      ) {
        finish(null);
        expireSession("login");
        return;
      }
      if (!response.ok || typeof payload?.grant_token !== "string") {
        const detail = payload?.message || payload?.error || payload?.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : `HTTP ${response.status} ${response.statusText}`,
        );
      }
      finish(payload.grant_token);
    } catch (requestError) {
      setPin("");
      setError(requestError.message);
      setBusy(false);
    }
  }

  const value = useMemo(() => ({ requestGrant }), [requestGrant]);

  return (
    <OperationGrantContext.Provider value={value}>
      {children}
      {prompt ? (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm">
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="operation-pin-title"
            onSubmit={submit}
            className="w-full max-w-sm rounded-2xl border border-white/10 bg-dna-surface p-5 shadow-2xl"
          >
            <h2 id="operation-pin-title" className="text-base font-semibold text-slate-100">
              Confirm {prompt.label}
            </h2>
            <p className="mt-1 text-sm text-slate-400">
              Enter your PIN. This approval expires after 30 seconds.
            </p>
            <label htmlFor="operation-pin" className="mt-4 block text-sm font-medium text-slate-200">
              PIN
            </label>
            <Input
              id="operation-pin"
              className="mt-2"
              type="password"
              autoComplete="current-password"
              maxLength={6}
              inputMode="numeric"
              pattern="[0-9]{6}"
              autoFocus
              value={pin}
              onChange={(event) => setPin(event.target.value)}
              disabled={busy}
            />
            {error ? <div className="mt-2 text-sm text-rose-300" role="alert">{error}</div> : null}
            <div className="mt-5 flex justify-end gap-2">
              <Btn variant="secondary" disabled={busy} onClick={() => finish(null)}>
                Cancel
              </Btn>
              <Btn type="submit" disabled={busy}>
                {busy ? "Checking…" : "Confirm"}
              </Btn>
            </div>
          </form>
        </div>
      ) : null}
    </OperationGrantContext.Provider>
  );
}
