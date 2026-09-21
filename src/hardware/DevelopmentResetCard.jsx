import React, { useState } from "react";
import { RotateCcw } from "lucide-react";
import Btn from "../components/Btn.jsx";
import Card from "../components/Card.jsx";
import Input from "../components/Input.jsx";
import useSecurity from "../security/useSecurity.js";
import useHardwareSimulator from "./useHardwareSimulator.js";

const RESET_REQUEST_KEY = "bass.hardware.resetRequestId";

function readPendingResetId() {
  try {
    return sessionStorage.getItem(RESET_REQUEST_KEY) || "";
  } catch {
    return "";
  }
}

function persistResetId(value) {
  try {
    sessionStorage.setItem(RESET_REQUEST_KEY, value);
    if (sessionStorage.getItem(RESET_REQUEST_KEY) !== value) {
      throw new Error("Reset retry state could not be verified.");
    }
  } catch (storageError) {
    throw new Error(
      `Reset was not started because this browser cannot safely save its retry ID. ${storageError.message}`,
    );
  }
}

function retireResetId() {
  try {
    sessionStorage.removeItem(RESET_REQUEST_KEY);
    if (sessionStorage.getItem(RESET_REQUEST_KEY) !== null) {
      throw new Error("The completed reset ID is still stored.");
    }
  } catch (storageError) {
    throw new Error(
      `The host completed the reset, but this browser could not retire its safety ID. ${storageError.message}`,
    );
  }
}

export default function DevelopmentResetCard({ buttonHeld, onReset }) {
  const simulator = useHardwareSimulator();
  const security = useSecurity();
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [completion, setCompletion] = useState("none");
  const serverPendingReset = simulator.status?.pending_reset;
  const serverPendingId = serverPendingReset?.request_id || "";
  const savedRequestId = readPendingResetId();
  const resumableRequestId = serverPendingId || savedRequestId;

  async function finishCompletedReset() {
    setCompletion("ready");
    onReset();
    security.expireSession("setup");
    await Promise.allSettled([
      simulator.refresh(),
      security.refreshAuthentication(),
    ]);
  }

  async function retryRetirement() {
    setBusy(true);
    setError("");
    try {
      retireResetId();
      await finishCompletedReset();
    } catch (storageError) {
      setCompletion("retirement_blocked");
      setError(
        `${storageError.message} No new reset will be started from this tab.`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function runReset() {
    if (confirmation !== "RESET") return;
    setBusy(true);
    setError("");
    try {
      let requestId = serverPendingId || readPendingResetId();
      if (!requestId) {
        requestId = crypto.randomUUID();
      }
      persistResetId(requestId);
      const result = await simulator.reset(requestId);
      if (
        result?.receipt?.request_id !== requestId ||
        !result.receipt.completed_at
      ) {
        throw new Error(
          "The host did not return a completed reset receipt. The original request ID was kept for recovery.",
        );
      }
      setConfirmation("");
      setOpen(false);
      try {
        retireResetId();
      } catch (storageError) {
        setCompletion("retirement_blocked");
        setError(
          `${storageError.message} No new reset will be started from this tab.`,
        );
        return;
      }
      await finishCompletedReset();
    } catch (requestError) {
      setError(
        `${requestError.message} Retry with the same request after the host is available.`,
      );
      await simulator.refresh().catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-rose-400/25 bg-rose-400/10 p-3 text-sm text-rose-200"
        >
          {error}
        </div>
      ) : null}
      {completion === "ready" ? (
        <div
          role="status"
          className="mb-4 rounded-xl border border-emerald-400/25 bg-emerald-400/10 p-3 text-sm text-emerald-200"
        >
          This reset request completed. The current device state is shown above.
        </div>
      ) : null}
      {completion === "retirement_blocked" ? (
        <div
          role="status"
          className="mb-4 rounded-xl border border-amber-400/25 bg-amber-400/10 p-3 text-sm text-amber-100"
        >
          <p>
            The host completed the reset, but this tab remains in recovery mode
            until its completed request ID can be removed. No new reset request
            will be issued.
          </p>
          <Btn
            className="mt-3"
            variant="secondary"
            disabled={busy}
            onClick={() => void retryRetirement()}
          >
            Retry browser cleanup
          </Btn>
        </div>
      ) : null}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-rose-200">
            <RotateCcw className="h-4 w-4" />
            Development reset
          </div>
          <p className="mt-2 max-w-3xl text-xs leading-relaxed text-slate-400">
            Creates a private backup, then clears product accounts, faces,
            PINs, logs, pairings, invitations, grants, ownership and recovery
            data. It keeps the device UUID and TLS identity and issues a new
            activation credential.
          </p>
        </div>
        <Btn
          variant="danger"
          disabled={busy || buttonHeld || completion === "retirement_blocked"}
          onClick={() => setOpen((value) => !value)}
        >
          {completion === "retirement_blocked"
            ? "Safety ID not retired"
            : resumableRequestId
              ? "Resume reset"
              : "Prepare reset"}
        </Btn>
      </div>

      {serverPendingId ? (
        <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/10 p-3 text-xs leading-relaxed text-amber-100">
          The host has a pending reset in phase “
          {serverPendingReset.phase || "recovery"}”. Type RESET below to resume
          this same request safely.
        </div>
      ) : savedRequestId ? (
        <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/10 p-3 text-xs leading-relaxed text-amber-100">
          This tab has an earlier reset request ID. Type RESET below to ask the
          host for its existing result before starting anything new.
        </div>
      ) : null}

      {open ? (
        <div className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/5 p-4">
          <label htmlFor="hardware-reset-confirmation" className="text-sm text-slate-200">
            Type <code className="font-bold text-rose-200">RESET</code> to confirm.
          </label>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <Input
              id="hardware-reset-confirmation"
              autoComplete="off"
              value={confirmation}
              disabled={busy}
              onChange={(event) => setConfirmation(event.target.value)}
              placeholder="RESET"
            />
            <Btn
              variant="danger"
              disabled={busy || confirmation !== "RESET"}
              onClick={() => void runReset()}
            >
              {busy ? "Backing up and resetting…" : "Back up and reset"}
            </Btn>
            <Btn
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setConfirmation("");
              }}
            >
              Cancel
            </Btn>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
