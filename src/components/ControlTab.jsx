import React, { useCallback, useEffect, useRef, useState } from "react";
import { ScanFace } from "lucide-react";
import Badge from "./Badge";
import Btn from "./Btn";
import Card from "./Card";
import useBackendCameraPreview from "../hooks/useBackendCameraPreview";

const FINAL_STATES = new Set([
  "granted",
  "denied",
  "error",
  "cancelled",
  "timeout",
  "completed",
]);

function normalizeScan(raw = {}, fallbackPurpose = "unlock") {
  const state =
    raw.state ||
    raw.result ||
    (raw.granted === true
      ? "granted"
      : raw.granted === false
        ? "denied"
        : "scanning");

  return {
    ok: raw.ok !== false,
    sessionId: raw.session_id || raw.sessionId || null,
    state,
    purpose: raw.purpose || fallbackPurpose,
    source: raw.camera_source || raw.source || null,
    user: raw.user || raw.candidate_user || raw.candidateUser || null,
    score: raw.score ?? raw.best_score ?? null,
    faceCount: raw.face_count ?? raw.faceCount ?? null,
    message: raw.message || raw.detail || raw.error || "",
    kind: raw.kind || "scan",
    count: raw.count ?? null,
    samplesNeeded: raw.samples_needed ?? raw.samplesNeeded ?? null,
    updatedAt: raw.updated_at ?? raw.updatedAt ?? Date.now(),
    window: raw.window || {
      matches: raw.matches ?? raw.candidate_count ?? 0,
      needed: raw.needed ?? raw.min_matches ?? 6,
      size: raw.size ?? raw.window_size ?? 10,
    },
  };
}

function cameraLabel(source, fallbackSource) {
  const resolvedSource =
    source === "pc_webcam" || source === "pi_camera" ? source : fallbackSource;
  if (resolvedSource === "pc_webcam") return "PC webcam";
  if (resolvedSource === "pi_camera") return "Pi camera";
  return "Backend camera";
}

function sessionTimestamp(value) {
  const numericValue = Number(value);
  if (Number.isFinite(numericValue) && numericValue > 0) return numericValue;
  const parsedValue = Date.parse(value || "");
  return Number.isFinite(parsedValue) ? parsedValue : null;
}

function scanBadge(state) {
  if (state === "granted" || state === "completed") return "ok";
  if (state === "denied" || state === "error" || state === "timeout") {
    return "err";
  }
  if (state === "cancelled") return "warn";
  return "info";
}

function scanLabel(state) {
  if (state === "granted") return "Granted";
  if (state === "denied") return "Denied";
  if (state === "error") return "Error";
  if (state === "timeout") return "Timeout";
  if (state === "cancelled") return "Cancelled";
  if (state === "completed") return "Completed";
  if (state === "cancelling") return "Cancelling";
  if (state === "starting") return "Starting";
  return "Scanning";
}

function confirmedCancellation(raw, purpose) {
  const cancelledScan = normalizeScan(raw, purpose);
  if (!cancelledScan.ok || cancelledScan.state !== "cancelled") {
    throw new Error(
      cancelledScan.message || "Backend did not confirm cancellation.",
    );
  }
  return cancelledScan;
}

export default function ControlTab({
  api,
  cameraSource,
  cameraAvailable = true,
  online,
  locked,
  ignitionOn,
  promptAutoLockSeconds = 0,
  doLock,
  doIgnitionStop,
  doFullReset,
  popToast,
  busy,
  onRefresh,
}) {
  const cameraPreview = useBackendCameraPreview(api, onRefresh);
  const pollTimerRef = useRef(null);
  const activeScanRef = useRef(null);
  const scanGenerationRef = useRef(0);
  const pendingStartCancellationRef = useRef(null);
  const unlockOwnerRef = useRef(null);
  const [scan, setScan] = useState(null);
  const [flowStage, setFlowStage] = useState("unlock_verify");
  const [promptCountdown, setPromptCountdown] = useState(null);
  const [unlockOwner, setUnlockOwner] = useState(null);

  const observedSession = cameraPreview.session
    ? normalizeScan(
        {
          ...cameraPreview.session,
          camera_source: cameraPreview.cameraSource,
        },
        cameraPreview.session.purpose || "unlock",
      )
    : null;
  const isScanning = Boolean(scan && !FINAL_STATES.has(scan.state));
  const observedMatchesOwn = Boolean(
    observedSession?.sessionId &&
      scan?.sessionId &&
      observedSession.sessionId === scan.sessionId,
  );
  const observedUpdatedAt = sessionTimestamp(observedSession?.updatedAt);
  const ownUpdatedAt = sessionTimestamp(scan?.updatedAt);
  const observedIsNewer =
    observedUpdatedAt != null &&
    (ownUpdatedAt == null || observedUpdatedAt > ownUpdatedAt);
  const showObservedSession = Boolean(
    observedSession &&
      (observedMatchesOwn ||
        (!isScanning &&
          (cameraPreview.active || !scan || observedIsNewer))),
  );
  const displayedSession = showObservedSession ? observedSession : scan;
  const displayedSessionActive = showObservedSession
    ? cameraPreview.active
    : isScanning;
  const effectiveCameraSource = cameraPreview.cameraSource || cameraSource;
  const activeCameraLabel = cameraLabel(
    displayedSession?.source,
    effectiveCameraSource,
  );
  const isEnrollment = displayedSession?.kind === "enroll";
  const canStartScan = online && cameraAvailable && !cameraPreview.active;
  const statusLine =
    displayedSession?.message ||
    (isEnrollment
      ? displayedSession?.state === "completed"
        ? `Face enrollment completed${displayedSession.user ? ` for ${displayedSession.user}` : ""}.`
        : displayedSessionActive
          ? `${activeCameraLabel} is enrolling${displayedSession?.user ? ` ${displayedSession.user}` : " a face"}.`
          : displayedSession
            ? `Face enrollment ${displayedSession.state || "finished"}.`
            : ""
      : displayedSession?.state === "granted"
        ? displayedSession.purpose === "ignition"
          ? `Ignition verified${displayedSession.user ? ` for ${displayedSession.user}` : ""}.`
          : `Unlock verified${displayedSession.user ? ` for ${displayedSession.user}` : ""}.`
        : displayedSession?.state === "denied"
          ? "Face scan was denied."
          : displayedSession?.state === "error"
            ? "Backend camera scan failed."
            : displayedSession?.state === "cancelled"
              ? "Scan cancelled."
              : displayedSession?.state === "timeout"
                ? "Face scan timed out."
                : displayedSessionActive
                  ? `${activeCameraLabel} is scanning.`
                  : !online
                    ? "Connect to the backend host to scan."
                    : !cameraAvailable
                      ? "The selected backend has no available camera."
                      : "");

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const invalidateScan = useCallback(() => {
    scanGenerationRef.current += 1;
    clearPoll();
    const activeScan = activeScanRef.current;
    activeScanRef.current = null;
    return {
      activeScan,
      generation: scanGenerationRef.current,
    };
  }, [clearPoll]);

  const cancelRemoteScan = useCallback(async (activeScan) => {
    if (!activeScan?.sessionId) return null;
    return activeScan.api.scanCancel(activeScan.sessionId);
  }, []);

  const refreshQuietly = useCallback(() => {
    if (typeof onRefresh === "function") void onRefresh({ silent: true });
  }, [onRefresh]);

  const handleFinalScan = useCallback(
    (next, generation) => {
      if (generation !== scanGenerationRef.current) return;
      clearPoll();
      if (activeScanRef.current?.generation === generation) {
        activeScanRef.current = null;
      }
      const verifiedCamera = cameraLabel(next.source, cameraSource);

      if (next.state === "granted") {
        refreshQuietly();
        if (next.purpose === "ignition") {
          popToast(
            "ok",
            "Ignition start",
            `${verifiedCamera} verified the same driver.`,
          );
          setFlowStage("unlock_verify");
        } else {
          unlockOwnerRef.current = next.user || null;
          setUnlockOwner(next.user || null);
          popToast("ok", "Device unlock", `${verifiedCamera} verified access.`);
          const max = Math.max(0, Number(promptAutoLockSeconds) || 0);
          setPromptCountdown(max > 0 ? max : null);
          setFlowStage("prompt");
        }
        return;
      }

      if (next.state === "cancelled") {
        popToast("info", "Scan cancelled", `${verifiedCamera} scan stopped.`);
      } else if (
        next.state === "denied" ||
        next.state === "timeout" ||
        next.state === "error"
      ) {
        popToast(
          "err",
          "Scan failed",
          next.message || `${verifiedCamera} did not grant access.`,
        );
      }
      setFlowStage("unlock_verify");
    },
    [
      cameraSource,
      clearPoll,
      popToast,
      promptAutoLockSeconds,
      refreshQuietly,
    ],
  );

  const applyScanUpdate = useCallback(
    (raw, fallbackPurpose, generation) => {
      if (generation !== scanGenerationRef.current) return null;
      const next = normalizeScan(raw, fallbackPurpose);
      setScan(next);
      if (FINAL_STATES.has(next.state)) handleFinalScan(next, generation);
      return next;
    },
    [handleFinalScan],
  );

  const pollScan = useCallback(
    (activeScan, purpose) => {
      clearPoll();
      const poll = async () => {
        if (
          activeScan.generation !== scanGenerationRef.current ||
          activeScanRef.current !== activeScan
        ) {
          return;
        }

        try {
          const raw = await activeScan.api.scanStatus(activeScan.sessionId);
          if (
            activeScan.generation !== scanGenerationRef.current ||
            activeScanRef.current !== activeScan
          ) {
            return;
          }
          const next = applyScanUpdate(raw, purpose, activeScan.generation);
          if (next && !FINAL_STATES.has(next.state)) {
            pollTimerRef.current = setTimeout(poll, 800);
          }
        } catch (error) {
          if (
            activeScan.generation !== scanGenerationRef.current ||
            activeScanRef.current !== activeScan
          ) {
            return;
          }
          const pollError = error.message || "Could not read scan status.";
          clearPoll();
          try {
            const cancelledRaw = await cancelRemoteScan(activeScan);
            if (
              activeScan.generation !== scanGenerationRef.current ||
              activeScanRef.current !== activeScan
            ) {
              return;
            }
            confirmedCancellation(cancelledRaw, purpose);
            const next = normalizeScan(
              {
                state: "error",
                session_id: activeScan.sessionId,
                message: `${pollError} Backend scan was cancelled.`,
              },
              purpose,
            );
            setScan(next);
            handleFinalScan(next, activeScan.generation);
          } catch (cancelError) {
            if (
              activeScan.generation !== scanGenerationRef.current ||
              activeScanRef.current !== activeScan
            ) {
              return;
            }
            const message = `${pollError} Backend cancellation failed: ${
              cancelError.message || "unknown error"
            }`;
            setScan(
              normalizeScan(
                {
                  state: "error",
                  session_id: activeScan.sessionId,
                  message,
                },
                purpose,
              ),
            );
            popToast("err", "Scan connection lost", message);
          }
        }
      };
      pollTimerRef.current = setTimeout(poll, 800);
    },
    [
      applyScanUpdate,
      cancelRemoteScan,
      clearPoll,
      handleFinalScan,
      popToast,
    ],
  );

  const startScan = useCallback(
    async (purpose) => {
      const requestApi = api;
      pendingStartCancellationRef.current = null;
      const { activeScan: previousScan, generation } = invalidateScan();
      if (previousScan) {
        activeScanRef.current = previousScan;
        try {
          const cancelledRaw = await cancelRemoteScan(previousScan);
          if (generation !== scanGenerationRef.current) return;
          confirmedCancellation(cancelledRaw, purpose);
          activeScanRef.current = null;
        } catch (error) {
          if (generation !== scanGenerationRef.current) return;
          activeScanRef.current = previousScan;
          const message = `Could not cancel the previous backend scan: ${
            error.message || "unknown error"
          }`;
          setScan(
            normalizeScan(
              {
                state: "error",
                session_id: previousScan.sessionId,
                message,
              },
              purpose,
            ),
          );
          popToast("err", "Scan blocked", message);
          return;
        }
      }
      if (generation !== scanGenerationRef.current) return;
      const expectedUser =
        purpose === "ignition" ? unlockOwnerRef.current : null;
      setScan(
        normalizeScan(
          {
            state: "starting",
            purpose,
            source: cameraSource,
            message:
              purpose === "ignition"
                ? "Starting ignition face verification."
                : "Starting face unlock.",
          },
          purpose,
        ),
      );

      try {
        const raw = await requestApi.scanStart({
          purpose,
          expected_user: expectedUser,
        });
        if (generation !== scanGenerationRef.current) {
          const staleSessionId = raw?.session_id || raw?.sessionId;
          let cancellationResult = null;
          let cancellationError = null;
          if (!staleSessionId) {
            cancellationError = new Error(
              "Backend returned no session to cancel.",
            );
          } else {
            try {
              cancellationResult = await requestApi.scanCancel(staleSessionId);
            } catch (error) {
              cancellationError = error;
            }
          }

          const pendingCancellation = pendingStartCancellationRef.current;
          if (
            pendingCancellation?.startGeneration === generation &&
            pendingCancellation.cancelGeneration === scanGenerationRef.current
          ) {
            pendingStartCancellationRef.current = null;
            try {
              if (cancellationError) throw cancellationError;
              const cancelledScan = confirmedCancellation(
                cancellationResult,
                purpose,
              );
              setScan(cancelledScan);
              popToast(
                "info",
                "Scan cancelled",
                "Backend camera scan stopped.",
              );
            } catch (error) {
              const message =
                error.message || "Could not confirm pending scan cancellation.";
              if (staleSessionId) {
                activeScanRef.current = {
                  api: requestApi,
                  generation: pendingCancellation.cancelGeneration,
                  sessionId: staleSessionId,
                };
              }
              setScan(
                normalizeScan(
                  {
                    state: "error",
                    session_id: staleSessionId,
                    message,
                  },
                  purpose,
                ),
              );
              popToast("err", "Cancel failed", message);
            }
          }
          return;
        }

        const next = applyScanUpdate(raw, purpose, generation);
        if (!next) return;
        if (!FINAL_STATES.has(next.state)) {
          if (!next.sessionId) {
            const missingSession = normalizeScan(
              {
                state: "error",
                purpose,
                source: cameraSource,
                message: "Backend did not return a scan session.",
              },
              purpose,
            );
            setScan(missingSession);
            handleFinalScan(missingSession, generation);
            return;
          }
          const activeScan = {
            api: requestApi,
            generation,
            sessionId: next.sessionId,
          };
          activeScanRef.current = activeScan;
          pollScan(activeScan, purpose);
        }
      } catch (error) {
        if (generation !== scanGenerationRef.current) {
          const pendingCancellation = pendingStartCancellationRef.current;
          if (
            pendingCancellation?.startGeneration === generation &&
            pendingCancellation.cancelGeneration === scanGenerationRef.current
          ) {
            pendingStartCancellationRef.current = null;
            const message = `Could not confirm pending scan cancellation: ${
              error.message || "scan start failed"
            }`;
            setScan(normalizeScan({ state: "error", message }, purpose));
            popToast("err", "Cancel unconfirmed", message);
          }
          return;
        }
        const next = normalizeScan(
          {
            state: "error",
            purpose,
            source: cameraSource,
            message: error.message || "Could not start scan.",
          },
          purpose,
        );
        setScan(next);
        handleFinalScan(next, generation);
      }
    },
    [
      api,
      applyScanUpdate,
      cameraSource,
      cancelRemoteScan,
      handleFinalScan,
      invalidateScan,
      pollScan,
      popToast,
    ],
  );

  const cancelScan = useCallback(async () => {
    const purpose = scan?.purpose || "unlock";
    const startGeneration = scanGenerationRef.current;
    const { activeScan, generation } = invalidateScan();
    if (!activeScan) {
      pendingStartCancellationRef.current = {
        cancelGeneration: generation,
        startGeneration,
      };
      setScan(
        normalizeScan(
          {
            state: "cancelling",
            message: "Waiting for the backend session so it can be cancelled.",
          },
          purpose,
        ),
      );
      popToast(
        "info",
        "Cancellation requested",
        "The scan will be cancelled as soon as the backend returns its session.",
      );
      return;
    }
    pendingStartCancellationRef.current = null;
    activeScanRef.current = activeScan;
    setScan(
      normalizeScan(
        {
          state: "cancelling",
          session_id: activeScan.sessionId,
          message: "Waiting for the backend to confirm cancellation.",
        },
        purpose,
      ),
    );
    try {
      const raw = await cancelRemoteScan(activeScan);
      if (generation !== scanGenerationRef.current) return;
      const cancelledScan = confirmedCancellation(raw, purpose);
      activeScanRef.current = null;
      setScan(cancelledScan);
      popToast("info", "Scan cancelled", "Backend camera scan stopped.");
    } catch (error) {
      if (generation !== scanGenerationRef.current) return;
      activeScanRef.current = activeScan;
      setScan(
        normalizeScan(
          {
            state: "error",
            session_id: activeScan.sessionId,
            message: error.message || "Could not cancel the backend scan.",
          },
          purpose,
        ),
      );
      popToast(
        "err",
        "Cancel failed",
        error.message || "Could not cancel the backend scan.",
      );
    }
  }, [cancelRemoteScan, invalidateScan, popToast, scan?.purpose]);

  const handleIgnitionPromptNo = useCallback(async () => {
    const ok = await doLock();
    if (ok) {
      unlockOwnerRef.current = null;
      setUnlockOwner(null);
      setPromptCountdown(null);
      setFlowStage("unlock_verify");
      setScan(null);
    }
  }, [doLock]);

  const handleIgnitionPromptYes = useCallback(async () => {
    setFlowStage("ignition_verify");
    setPromptCountdown(null);
    await startScan("ignition");
  }, [startScan]);

  const handleFullReset = useCallback(async () => {
    pendingStartCancellationRef.current = null;
    const { activeScan, generation } = invalidateScan();
    if (activeScan) {
      activeScanRef.current = activeScan;
      try {
        const cancelledRaw = await cancelRemoteScan(activeScan);
        confirmedCancellation(cancelledRaw, scan?.purpose || "unlock");
        if (generation === scanGenerationRef.current) {
          activeScanRef.current = null;
        }
      } catch {
        if (generation === scanGenerationRef.current) {
          activeScanRef.current = activeScan;
        }
      }
    }
    if (generation !== scanGenerationRef.current) return;
    const ok = await doFullReset();
    if (generation !== scanGenerationRef.current) return;
    if (ok) {
      activeScanRef.current = null;
      unlockOwnerRef.current = null;
      setUnlockOwner(null);
      setPromptCountdown(null);
      setFlowStage("unlock_verify");
      setScan(null);
    }
  }, [cancelRemoteScan, doFullReset, invalidateScan, scan?.purpose]);

  useEffect(() => {
    if (!locked) return undefined;
    unlockOwnerRef.current = null;
    const timer = setTimeout(() => {
      setUnlockOwner(null);
      setPromptCountdown(null);
      setFlowStage("unlock_verify");
    }, 0);
    return () => clearTimeout(timer);
  }, [locked]);

  useEffect(() => {
    const resetTimer = setTimeout(() => {
      setScan(null);
      unlockOwnerRef.current = null;
      setUnlockOwner(null);
      setPromptCountdown(null);
      setFlowStage("unlock_verify");
    }, 0);

    return () => {
      clearTimeout(resetTimer);
      pendingStartCancellationRef.current = null;
      const { activeScan } = invalidateScan();
      if (activeScan) {
        void cancelRemoteScan(activeScan).catch(() => null);
      }
    };
  }, [api, cancelRemoteScan, invalidateScan]);

  useEffect(() => {
    if (flowStage !== "prompt" || promptCountdown == null) return undefined;
    const timer = setTimeout(() => {
      setPromptCountdown((current) => {
        if (current == null) return current;
        if (current <= 1) {
          setTimeout(() => void handleIgnitionPromptNo(), 0);
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => clearTimeout(timer);
  }, [flowStage, handleIgnitionPromptNo, promptCountdown]);

  return (
    <div className="w-full pt-1">
      <Card contentClassName="p-4 sm:p-6">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-violet-500/25 bg-violet-500/10">
            <ScanFace
              className="h-5 w-5 text-violet-400"
              strokeWidth={1.75}
            />
          </div>
          <div className="mt-4 min-w-0 max-w-lg">
            <div className="text-lg font-semibold tracking-tight text-slate-100">
              {activeCameraLabel} face scan
            </div>
          </div>
        </div>

        <div className="mx-auto mt-5 max-w-2xl overflow-hidden rounded-2xl border border-white/10 bg-black/40">
          <div className="flex aspect-video items-center justify-center">
            {cameraPreview.streamUrl ? (
              <img
                key={cameraPreview.streamKey}
                src={cameraPreview.streamUrl}
                alt={`Live ${activeCameraLabel} frame`}
                className="h-full w-full object-contain"
                onError={cameraPreview.onStreamError}
              />
            ) : (
              <div
                className={`px-5 text-center text-sm leading-relaxed ${
                  cameraPreview.previewError
                    ? "text-rose-200/90"
                    : "text-slate-500"
                }`}
              >
                {cameraPreview.previewError ||
                  (cameraPreview.active
                    ? "Waiting for the next camera frame…"
                    : online
                      ? "Camera idle"
                      : "Connect to the backend host to view its camera.")}
              </div>
            )}
          </div>
        </div>

        <div className="mx-auto mt-5 max-w-2xl rounded-2xl border border-white/10 bg-dna-bg/70 px-4 py-4 text-center sm:mt-6 sm:px-5 sm:py-5">
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Badge variant={scanBadge(displayedSession?.state || "idle")}>
              {displayedSession
                ? scanLabel(displayedSession.state)
                : "Ready"}
            </Badge>
            <Badge variant={locked ? "warn" : "ok"}>
              {locked ? "Locked" : "Unlocked"}
            </Badge>
            <Badge variant={ignitionOn ? "ok" : "default"}>
              {ignitionOn ? "Ignition on" : "Ignition off"}
            </Badge>
          </div>

          {statusLine ? (
            <div className="mt-4 text-sm font-medium text-slate-100">
              {statusLine}
            </div>
          ) : null}

          {displayedSession ? (
            <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-3">
              <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2">
                <div className="text-slate-500">User</div>
                <div className="mt-1 truncate text-slate-200">
                  {displayedSession.user || "None"}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2">
                <div className="text-slate-500">
                  {isEnrollment ? "Faces" : "Score"}
                </div>
                <div className="mt-1 text-slate-200">
                  {isEnrollment
                    ? (displayedSession.faceCount ?? 0)
                    : displayedSession.score == null
                      ? "Pending"
                      : displayedSession.score}
                </div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2">
                <div className="text-slate-500">
                  {isEnrollment ? "Samples" : "Matches"}
                </div>
                <div className="mt-1 text-slate-200">
                  {isEnrollment ? (
                    <>
                      {displayedSession.count ?? 0}/
                      {displayedSession.samplesNeeded ?? "?"}
                    </>
                  ) : (
                    <>
                      {displayedSession.window?.matches ?? 0}/
                      {displayedSession.window?.size ?? 10}
                      <span className="text-slate-500">
                        {" · "}
                        {displayedSession.window?.needed ?? 6} required
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="mt-4 grid grid-cols-1 gap-2 sm:flex sm:flex-wrap sm:justify-center">
          {isScanning ? (
            <Btn
              variant="secondary"
              disabled={busy || scan?.state === "cancelling"}
              onClick={cancelScan}
              className="w-full sm:w-auto"
            >
              {scan?.state === "cancelling" ? "Cancelling…" : "Cancel scan"}
            </Btn>
          ) : flowStage === "prompt" ? null : (
            <Btn
              disabled={busy || !canStartScan}
              onClick={() => startScan("unlock")}
              className="w-full sm:w-auto"
            >
              {cameraPreview.active
                ? "Backend camera busy"
                : "Start face unlock"}
            </Btn>
          )}
          <Btn
            variant="danger"
            disabled={busy}
            onClick={handleFullReset}
            className="w-full sm:w-auto"
          >
            FULL RESET
          </Btn>
          {ignitionOn ? (
            <Btn
              variant="secondary"
              disabled={busy}
              onClick={doIgnitionStop}
              className="w-full sm:w-auto"
            >
              Stop ignition
            </Btn>
          ) : null}
        </div>

        {flowStage === "prompt" ? (
          <div className="mx-auto mt-4 flex max-w-2xl flex-wrap items-center justify-center gap-2 rounded-xl border border-violet-500/25 bg-violet-500/10 px-4 py-3">
            <div className="w-full text-center text-sm text-violet-200">
              Start ignition now? The backend camera must verify{" "}
              {unlockOwner || "the same driver"} again.
            </div>
            {promptAutoLockSeconds > 0 ? (
              <div className="w-full text-center text-xs text-violet-300/90">
                Auto lock in {promptCountdown ?? promptAutoLockSeconds}s if no
                choice.
              </div>
            ) : null}
            <Btn
              disabled={busy || isScanning || !canStartScan}
              onClick={handleIgnitionPromptYes}
            >
              Yes, verify ignition
            </Btn>
            <Btn
              variant="secondary"
              disabled={busy || isScanning}
              onClick={handleIgnitionPromptNo}
            >
              No, lock now
            </Btn>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
