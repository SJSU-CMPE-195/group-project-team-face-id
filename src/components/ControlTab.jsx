import React, { useCallback, useEffect, useRef, useState } from "react";
import { ScanFace } from "lucide-react";
import Badge from "./Badge";
import Btn from "./Btn";
import Card from "./Card";
import useBackendCameraPreview from "../hooks/useBackendCameraPreview";
import useOperationGrant from "../security/useOperationGrant.js";
import { actuatorLabels } from "../utils/actuatorStatus.js";
import {
  cameraLabel,
  confirmedCancellation,
  FINAL_SCAN_STATES,
  normalizeScan,
  scanBadge,
  scanLabel,
  sessionTimestamp,
} from "../utils/scanState.js";

export default function ControlTab({
  api,
  currentUser,
  cameraSource,
  cameraAvailable = true,
  online,
  locked,
  ignitionOn,
  simulatedActuators,
  actuatorControlAvailable,
  physicalStateConfirmed,
  promptAutoLockSeconds = 0,
  doLock,
  doIgnitionStop,
  doFullReset,
  popToast,
  busy,
  onRefresh,
}) {
  const { requestGrant } = useOperationGrant();
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
  const isScanning = Boolean(scan && !FINAL_SCAN_STATES.has(scan.state));
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
  const canStartScan =
    online &&
    cameraAvailable &&
    actuatorControlAvailable &&
    !cameraPreview.active;
  const labels = actuatorLabels({
    online,
    locked,
    ignitionOn,
    simulatedActuators,
    actuatorControlAvailable,
    physicalStateConfirmed,
  });
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
                      : !actuatorControlAvailable
                        ? "Real Pi outputs are blocked until actuator feedback is available."
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
      if (FINAL_SCAN_STATES.has(next.state)) {
        handleFinalScan(next, generation);
      }
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
          if (next && !FINAL_SCAN_STATES.has(next.state)) {
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
      if (!online || !actuatorControlAvailable) {
        popToast(
          "info",
          "Physical outputs blocked",
          "Real Pi lock and ignition controls require command acknowledgement and position feedback.",
        );
        return;
      }
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
      let grantToken;
      try {
        grantToken = await requestGrant(
          `scan.${purpose}`,
          currentUser.id,
          purpose === "ignition" ? "ignition scan" : "unlock scan",
        );
      } catch (error) {
        popToast("err", "PIN request failed", error.message);
        return;
      }
      if (!grantToken || generation !== scanGenerationRef.current) return;
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
          expected_user_id: currentUser.id,
        }, grantToken);
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
        if (!FINAL_SCAN_STATES.has(next.state)) {
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
      currentUser.id,
      handleFinalScan,
      invalidateScan,
      pollScan,
      popToast,
      requestGrant,
      actuatorControlAvailable,
      online,
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
    if (!online || !actuatorControlAvailable) return;
    const ok = await doLock();
    if (ok) {
      unlockOwnerRef.current = null;
      setUnlockOwner(null);
      setPromptCountdown(null);
      setFlowStage("unlock_verify");
      setScan(null);
    }
  }, [actuatorControlAvailable, doLock, online]);

  const handleIgnitionPromptYes = useCallback(async () => {
    if (!online || !actuatorControlAvailable) return;
    setFlowStage("ignition_verify");
    setPromptCountdown(null);
    await startScan("ignition");
  }, [actuatorControlAvailable, online, startScan]);

  const handleFullReset = useCallback(async () => {
    if (!online || !actuatorControlAvailable) return;
    const generation = scanGenerationRef.current;
    const ok = await doFullReset();
    if (!ok || generation !== scanGenerationRef.current) return;
    pendingStartCancellationRef.current = null;
    invalidateScan();
    activeScanRef.current = null;
    unlockOwnerRef.current = null;
    setUnlockOwner(null);
    setPromptCountdown(null);
    setFlowStage("unlock_verify");
    setScan(null);
  }, [
    actuatorControlAvailable,
    doFullReset,
    invalidateScan,
    online,
  ]);

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
    if (
      !online ||
      !actuatorControlAvailable ||
      flowStage !== "prompt" ||
      promptCountdown == null
    ) {
      return undefined;
    }
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
  }, [
    actuatorControlAvailable,
    flowStage,
    handleIgnitionPromptNo,
    online,
    promptCountdown,
  ]);

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
                : !online
                  ? "Offline"
                  : actuatorControlAvailable
                    ? "Ready"
                    : "Controls blocked"}
            </Badge>
            <Badge
              variant={
                simulatedActuators
                  ? "info"
                  : physicalStateConfirmed
                    ? locked
                      ? "warn"
                      : "ok"
                    : "warn"
              }
            >
              {labels.lockLong}
            </Badge>
            <Badge
              variant={
                simulatedActuators
                  ? "info"
                  : physicalStateConfirmed && ignitionOn
                    ? "ok"
                    : "default"
              }
            >
              {labels.ignitionLong}
            </Badge>
          </div>

          {online && !actuatorControlAvailable ? (
            <div className="mt-4 text-sm leading-relaxed text-amber-200/90">
              Real Pi lock and ignition outputs are blocked. Enrollment and
              administrator data tasks remain available.
            </div>
          ) : null}

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
                : simulatedActuators
                  ? "Start face unlock (simulated output)"
                  : "Start face unlock"}
            </Btn>
          )}
          <Btn
            variant="danger"
            disabled={busy || !online || !actuatorControlAvailable}
            onClick={handleFullReset}
            className="w-full sm:w-auto"
          >
            {simulatedActuators ? "FULL RESET (SIMULATED)" : "FULL RESET"}
          </Btn>
          {online && actuatorControlAvailable && ignitionOn ? (
            <Btn
              variant="secondary"
              disabled={busy || !online || !actuatorControlAvailable}
              onClick={doIgnitionStop}
              className="w-full sm:w-auto"
            >
              {simulatedActuators
                ? "Stop ignition (simulated)"
                : "Stop ignition"}
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
              disabled={
                busy ||
                isScanning ||
                !canStartScan ||
                !actuatorControlAvailable
              }
              onClick={handleIgnitionPromptYes}
            >
              Yes, verify ignition
            </Btn>
            <Btn
              variant="secondary"
              disabled={
                busy || isScanning || !online || !actuatorControlAvailable
              }
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
