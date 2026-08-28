import React, { useCallback, useEffect, useRef, useState } from "react";
import Card from "./Card";
import Btn from "./Btn";
import Badge from "./Badge";
import { ScanFace } from "lucide-react";
import { isFaceAccessAllowed } from "../utils/helpers";

const WINDOW_SIZE = 10;
const MIN_MATCHES = 6;
const FINAL_STATES = new Set(["granted", "denied", "error", "cancelled", "timeout"]);

function evaluateWindow(history) {
  const validUsers = history.filter((h) => h.matched && h.user).map((h) => h.user);
  if (!validUsers.length) return { granted: false, candidateUser: null, candidateCount: 0 };
  const counts = {};
  for (const user of validUsers) counts[user] = (counts[user] || 0) + 1;
  let candidateUser = null;
  let candidateCount = 0;
  for (const [user, count] of Object.entries(counts)) {
    if (count > candidateCount) {
      candidateUser = user;
      candidateCount = count;
    }
  }
  return { granted: candidateCount >= MIN_MATCHES, candidateUser, candidateCount };
}

function formatFetchError(data, status, statusText) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  if (detail != null) return JSON.stringify(detail);
  return `HTTP ${status} ${statusText}`;
}

function normalizeScan(raw = {}, fallbackPurpose = "unlock") {
  const state =
    raw.state ||
    raw.result ||
    (raw.granted === true ? "granted" : raw.granted === false ? "denied" : "scanning");
  return {
    ok: raw.ok !== false,
    sessionId: raw.session_id || raw.sessionId || null,
    state,
    purpose: raw.purpose || fallbackPurpose,
    user: raw.user || raw.candidate_user || raw.candidateUser || null,
    score: raw.score ?? raw.best_score ?? null,
    faceCount: raw.face_count ?? raw.faceCount ?? null,
    message: raw.message || raw.detail || raw.error || "",
    window: raw.window || {
      matches: raw.matches ?? raw.candidate_count ?? 0,
      needed: raw.needed ?? raw.min_matches ?? 6,
      size: raw.size ?? raw.window_size ?? 10,
    },
  };
}

function scanBadge(state) {
  if (state === "granted") return "ok";
  if (state === "denied" || state === "error" || state === "timeout") return "err";
  if (state === "cancelled") return "warn";
  return "info";
}

function scanLabel(state) {
  if (state === "granted") return "Granted";
  if (state === "denied") return "Denied";
  if (state === "error") return "Error";
  if (state === "timeout") return "Timeout";
  if (state === "cancelled") return "Cancelled";
  if (state === "starting") return "Starting";
  return "Scanning";
}

function isMockPiUrl(baseUrl) {
  return (baseUrl || "").trim().replace(/\/$/, "").toLocaleLowerCase() === "fake://pi";
}

export default function ControlTab({
  api,
  mode,
  baseUrl,
  faceApiUrl,
  faceAccessAllowed = {},
  locked,
  ignitionOn,
  promptAutoLockSeconds = 0,
  doUnlock,
  doLock,
  doIgnitionStop,
  doFullReset,
  popToast,
  busy,
  onRefresh,
}) {
  const usePiScan = mode === "device";
  const isMockApi = usePiScan && isMockPiUrl(baseUrl);

  if (!usePiScan) {
    return (
      <LocalCameraControl
        mode={mode}
        faceApiUrl={faceApiUrl}
        faceAccessAllowed={faceAccessAllowed}
        locked={locked}
        ignitionOn={ignitionOn}
        doUnlock={doUnlock}
        doIgnitionStop={doIgnitionStop}
        doFullReset={doFullReset}
        popToast={popToast}
        busy={busy}
      />
    );
  }

  return (
    <PiCameraControl
      api={api}
      mode={mode}
      locked={locked}
      ignitionOn={ignitionOn}
      promptAutoLockSeconds={promptAutoLockSeconds}
      doLock={doLock}
      doIgnitionStop={doIgnitionStop}
      doFullReset={doFullReset}
      popToast={popToast}
      busy={busy}
      onRefresh={onRefresh}
      isMockApi={isMockApi}
    />
  );
}

function PiCameraControl({
  api,
  mode,
  locked,
  ignitionOn,
  promptAutoLockSeconds,
  doLock,
  doIgnitionStop,
  doFullReset,
  popToast,
  busy,
  onRefresh,
  isMockApi,
}) {
  const pollTimerRef = useRef(null);
  const scanSessionRef = useRef(null);
  const unlockOwnerRef = useRef(null);
  const [scan, setScan] = useState(null);
  const [flowStage, setFlowStage] = useState("unlock_verify");
  const [promptCountdown, setPromptCountdown] = useState(null);
  const [unlockOwner, setUnlockOwner] = useState(null);

  const isScanning = scan && !FINAL_STATES.has(scan.state);
  const statusLine =
    scan?.state === "granted"
      ? scan.purpose === "ignition"
        ? `Ignition verified${scan.user ? ` for ${scan.user}` : ""}.`
        : `Unlock verified${scan.user ? ` for ${scan.user}` : ""}.`
      : scan?.state === "denied"
      ? scan.message || "Face scan was denied."
      : scan?.state === "error"
      ? scan.message || "Pi scan failed."
      : scan?.state === "cancelled"
      ? "Scan cancelled."
      : isScanning
      ? scan.message || "Pi camera is scanning."
      : mode === "device"
      ? isMockApi
        ? "Ready for Fake Pi scan."
        : "Ready for Pi camera scan."
      : "Simulation mode will grant a mock scan.";

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const refreshQuietly = useCallback(() => {
    if (typeof onRefresh === "function") void onRefresh({ silent: true });
  }, [onRefresh]);

  const handleFinalScan = useCallback(
    (next) => {
      clearPoll();
      scanSessionRef.current = null;

      if (next.state === "granted") {
        refreshQuietly();
        if (next.purpose === "ignition") {
          popToast("ok", "Ignition start", isMockApi ? "Fake Pi verified the same driver." : "Pi camera verified the same driver.");
          setFlowStage("unlock_verify");
        } else {
          unlockOwnerRef.current = next.user || null;
          setUnlockOwner(next.user || null);
          popToast("ok", "Device unlock", isMockApi ? "Fake Pi verified access." : "Pi camera verified access.");
          {
            const max = Math.max(0, Number(promptAutoLockSeconds) || 0);
            setPromptCountdown(max > 0 ? max : null);
          }
          setFlowStage("prompt");
        }
        return;
      }

      if (next.state === "cancelled") {
        popToast("info", "Scan cancelled", isMockApi ? "Fake Pi scan stopped." : "Pi camera scan stopped.");
        setFlowStage("unlock_verify");
        return;
      }

      if (next.state === "denied" || next.state === "timeout" || next.state === "error") {
        popToast("err", "Scan failed", next.message || (isMockApi ? "Fake Pi did not grant access." : "Pi camera did not grant access."));
        setFlowStage("unlock_verify");
      }
    },
    [clearPoll, isMockApi, popToast, promptAutoLockSeconds, refreshQuietly],
  );

  const applyScanUpdate = useCallback(
    (raw, fallbackPurpose) => {
      const next = normalizeScan(raw, fallbackPurpose);
      setScan(next);
      if (FINAL_STATES.has(next.state)) handleFinalScan(next);
      return next;
    },
    [handleFinalScan],
  );

  const pollScan = useCallback(
    (sessionId, purpose) => {
      clearPoll();
      if (!sessionId) return;
      pollTimerRef.current = setInterval(async () => {
        try {
          const raw = await api.scanStatus(sessionId);
          applyScanUpdate(raw, purpose);
        } catch (e) {
          const next = normalizeScan(
            { state: "error", session_id: sessionId, message: e.message || "Could not read scan status." },
            purpose,
          );
          setScan(next);
          handleFinalScan(next);
        }
      }, 800);
    },
    [api, applyScanUpdate, clearPoll, handleFinalScan],
  );

  const startScan = useCallback(
    async (purpose) => {
      clearPoll();
      const expectedUser = purpose === "ignition" ? unlockOwnerRef.current : null;
      const starting = normalizeScan(
        {
          state: "starting",
          purpose,
          message: purpose === "ignition" ? "Starting ignition verification." : "Starting unlock verification.",
        },
        purpose,
      );
      setScan(starting);

      try {
        const raw = await api.scanStart({
          purpose,
          expectedUser,
          expected_user: expectedUser,
        });
        const next = applyScanUpdate(raw, purpose);
        if (!FINAL_STATES.has(next.state)) {
          scanSessionRef.current = next.sessionId;
          pollScan(next.sessionId, purpose);
        } else {
          scanSessionRef.current = null;
        }
      } catch (e) {
        const next = normalizeScan({ state: "error", purpose, message: e.message || "Could not start scan." }, purpose);
        setScan(next);
        handleFinalScan(next);
      }
    },
    [api, applyScanUpdate, clearPoll, handleFinalScan, pollScan],
  );

  const cancelScan = useCallback(async () => {
    const sessionId = scanSessionRef.current || scan?.sessionId;
    clearPoll();
    if (!sessionId) {
      setScan(normalizeScan({ state: "cancelled" }, scan?.purpose || "unlock"));
      return;
    }
    try {
      const raw = await api.scanCancel(sessionId);
      applyScanUpdate(raw, scan?.purpose || "unlock");
    } catch (e) {
      const next = normalizeScan({ state: "cancelled", session_id: sessionId, message: e.message }, scan?.purpose || "unlock");
      setScan(next);
      handleFinalScan(next);
    }
  }, [api, applyScanUpdate, clearPoll, handleFinalScan, scan]);

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
    clearPoll();
    const ok = await doFullReset();
    if (ok) {
      unlockOwnerRef.current = null;
      setUnlockOwner(null);
      setPromptCountdown(null);
      setFlowStage("unlock_verify");
      setScan(null);
    }
  }, [clearPoll, doFullReset]);

  useEffect(() => {
    if (locked) {
      unlockOwnerRef.current = null;
      const timer = setTimeout(() => {
        setUnlockOwner(null);
        setPromptCountdown(null);
        setFlowStage("unlock_verify");
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [locked]);

  useEffect(() => () => clearPoll(), [clearPoll]);

  useEffect(() => {
    if (flowStage !== "prompt" || promptCountdown == null) return;
    const timer = setTimeout(() => {
      setPromptCountdown((current) => {
        if (current == null) return current;
        if (current <= 1) {
          setTimeout(() => {
            void handleIgnitionPromptNo();
          }, 0);
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
            <ScanFace className="h-5 w-5 text-violet-400" strokeWidth={1.75} />
          </div>
          <div className="mt-4 min-w-0 max-w-lg">
            <div className="text-lg font-semibold tracking-tight text-slate-100">{isMockApi ? "Fake Pi scan" : "Pi camera scan"}</div>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-400">
              {isMockApi
                ? "The in-browser Fake Pi exercises scan sessions without a server."
                : "Device API mode starts a scan on the Pi and waits for the Pi to return the result."}
            </p>
          </div>
        </div>

        <div className="mx-auto mt-5 max-w-2xl rounded-2xl border border-white/10 bg-dna-bg/70 px-4 py-4 text-center sm:mt-6 sm:px-5 sm:py-5">
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Badge variant={scanBadge(scan?.state || "idle")}>{scan ? scanLabel(scan.state) : "Ready"}</Badge>
            <Badge variant={locked ? "warn" : "ok"}>{locked ? "Locked" : "Unlocked"}</Badge>
            <Badge variant={ignitionOn ? "ok" : "default"}>{ignitionOn ? "Ignition on" : "Ignition off"}</Badge>
          </div>

          <div className="mt-4 text-sm font-medium text-slate-100">{statusLine}</div>

          {scan ? (
            <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-3">
              <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2">
                <div className="text-slate-500">Driver</div>
                <div className="mt-1 truncate text-slate-200">{scan.user || "None"}</div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2">
                <div className="text-slate-500">Score</div>
                <div className="mt-1 text-slate-200">{scan.score == null ? "Pending" : scan.score}</div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2">
                <div className="text-slate-500">Window</div>
                <div className="mt-1 text-slate-200">
                  {scan.window?.matches ?? 0}/{scan.window?.size ?? 10}
                  <span className="text-slate-500"> need {scan.window?.needed ?? 6}</span>
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="mt-4 grid grid-cols-1 gap-2 sm:flex sm:flex-wrap sm:justify-center">
          {isScanning ? (
            <Btn variant="secondary" disabled={busy} onClick={cancelScan} className="w-full sm:w-auto">
              Cancel scan
            </Btn>
          ) : flowStage === "prompt" ? null : (
            <Btn disabled={busy} onClick={() => startScan("unlock")} className="w-full sm:w-auto">
              {isMockApi ? "Start fake Pi scan" : "Start Pi face scan"}
            </Btn>
          )}
          <Btn variant="danger" disabled={busy} onClick={handleFullReset} className="w-full sm:w-auto">
            FULL RESET
          </Btn>
          {ignitionOn ? (
            <Btn variant="secondary" disabled={busy} onClick={doIgnitionStop} className="w-full sm:w-auto">
              Stop ignition
            </Btn>
          ) : null}
        </div>

        {flowStage === "prompt" ? (
          <div className="mx-auto mt-4 flex max-w-2xl flex-wrap items-center justify-center gap-2 rounded-xl border border-violet-500/25 bg-violet-500/10 px-4 py-3">
            <div className="w-full text-center text-sm text-violet-200">
              Start ignition now? The second scan must match {unlockOwner || "the same driver"}.
            </div>
            {promptAutoLockSeconds > 0 ? (
              <div className="w-full text-center text-xs text-violet-300/90">
                Auto lock in {promptCountdown ?? promptAutoLockSeconds}s if no choice.
              </div>
            ) : (
              <div className="w-full text-center text-xs text-slate-500">No auto-lock timer; choose Yes or No when ready.</div>
            )}
            <Btn disabled={busy || isScanning} onClick={handleIgnitionPromptYes}>
              Yes, verify ignition
            </Btn>
            <Btn variant="secondary" disabled={busy || isScanning} onClick={handleIgnitionPromptNo}>
              No, lock now
            </Btn>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function LocalCameraControl({
  mode,
  faceApiUrl,
  faceAccessAllowed = {},
  locked,
  ignitionOn,
  doUnlock,
  doIgnitionStop,
  doFullReset,
  popToast,
  busy,
}) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const intervalRef = useRef(null);
  const inFlightRef = useRef(false);
  const historyRef = useRef([]);
  const grantedRef = useRef(false);

  const [camOn, setCamOn] = useState(false);
  const [statusLine, setStatusLine] = useState("Camera off");
  const [enrolledHint, setEnrolledHint] = useState("");
  const [apiError, setApiError] = useState(null);

  const cleanApi = (faceApiUrl || "").trim().replace(/\/$/, "");

  const clearScanLoop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const stopCamera = useCallback(() => {
    clearScanLoop();
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
    historyRef.current = [];
    grantedRef.current = false;
    setCamOn(false);
    setStatusLine("Camera off");
    setApiError(null);
  }, [clearScanLoop]);

  const startCamera = useCallback(async () => {
    if (!cleanApi) {
      popToast("err", "Face API", "Set Face API URL under Connection first.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      historyRef.current = [];
      grantedRef.current = false;
      setCamOn(true);
      setStatusLine("Scanning with this device camera.");
      setApiError(null);
    } catch (e) {
      popToast("err", "Camera", e.message || "Permission denied");
    }
  }, [cleanApi, popToast]);

  const handleFullReset = useCallback(async () => {
    const ok = await doFullReset();
    if (ok) stopCamera();
  }, [doFullReset, stopCamera]);

  useEffect(() => () => stopCamera(), [stopCamera]);

  useEffect(() => {
    if (!cleanApi) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${cleanApi}/api/face-status`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setEnrolledHint(data.count > 0 ? `Enrolled: ${data.enrolled.join(", ")}` : "No users in face DB.");
      } catch {
        if (!cancelled) setEnrolledHint("Cannot reach Face API.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cleanApi, camOn]);

  useEffect(() => {
    if (!camOn || !cleanApi) {
      clearScanLoop();
      return;
    }

    const tick = async () => {
      if (inFlightRef.current || grantedRef.current) return;
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || video.readyState < 2) return;

      inFlightRef.current = true;
      try {
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (width < 2 || height < 2) return;

        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").drawImage(video, 0, 0, width, height);
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
        if (!blob) return;

        const form = new FormData();
        form.append("image", blob, "frame.jpg");
        const res = await fetch(`${cleanApi}/api/verify-frame`, { method: "POST", body: form });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setApiError(formatFetchError(data, res.status, res.statusText));
          setStatusLine("Recognition API error");
          return;
        }

        setApiError(null);
        const history = historyRef.current;
        const faceCount = data.face_count ?? 0;
        const allowed = faceCount === 1 && !!data.matched && isFaceAccessAllowed(data.user, faceAccessAllowed);
        history.push({ matched: allowed, user: allowed ? data.user : null });
        while (history.length > WINDOW_SIZE) history.shift();

        const { granted, candidateUser, candidateCount } = evaluateWindow(history);
        if (faceCount === 0) {
          setStatusLine("No face detected");
        } else if (faceCount > 1) {
          setStatusLine("Multiple faces in frame");
        } else if (data.matched && !allowed) {
          setStatusLine(`Recognized ${data.user} but access is off. ${candidateCount}/${WINDOW_SIZE}`);
        } else {
          setStatusLine(
            data.matched
              ? `Match: ${data.user} (${data.score}) ${candidateCount}/${WINDOW_SIZE}`
              : `No match. Closest ${data.user ?? "none"} (${data.score})`,
          );
        }

        if (granted && !grantedRef.current) {
          grantedRef.current = true;
          const ok = await doUnlock({ suppressSuccessToast: true });
          if (ok) {
            popToast("ok", "Device unlock", `Verified ${candidateUser || "driver"} with this device camera.`);
            stopCamera();
          } else {
            grantedRef.current = false;
          }
        }
      } catch (e) {
        setApiError(e.message);
        setStatusLine("Network error");
      } finally {
        inFlightRef.current = false;
      }
    };

    intervalRef.current = setInterval(tick, 450);
    tick();
    return () => clearScanLoop();
  }, [camOn, cleanApi, clearScanLoop, doUnlock, faceAccessAllowed, popToast, stopCamera]);

  return (
    <div className="w-full pt-1">
      <Card contentClassName="p-4 sm:p-6">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-violet-500/25 bg-violet-500/10">
            <ScanFace className="h-5 w-5 text-violet-400" strokeWidth={1.75} />
          </div>
          <div className="mt-4 min-w-0 max-w-lg">
            <div className="text-lg font-semibold tracking-tight text-slate-100">Local camera scan</div>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-400">
              {mode === "device"
                ? "Device API mode sends face scans to the configured Pi camera."
                : "Simulation mode keeps the browser camera unlock flow."}
            </p>
          </div>
        </div>

        <div className="mx-auto mt-6 max-w-2xl overflow-hidden rounded-2xl border border-white/10 bg-black/40">
          <video ref={videoRef} className="aspect-video w-full object-cover" playsInline muted />
          <canvas ref={canvasRef} className="hidden" aria-hidden="true" />
        </div>

        <div className="mt-4 grid grid-cols-1 gap-2 sm:flex sm:flex-wrap sm:justify-center">
          {!camOn ? (
            <Btn disabled={busy} onClick={startCamera} className="w-full sm:w-auto">
              Turn on camera and scan
            </Btn>
          ) : (
            <Btn variant="secondary" disabled={busy} onClick={stopCamera} className="w-full sm:w-auto">
              Stop camera
            </Btn>
          )}
          <Btn variant="danger" disabled={busy} onClick={handleFullReset} className="w-full sm:w-auto">
            FULL RESET
          </Btn>
          {ignitionOn ? (
            <Btn variant="secondary" disabled={busy} onClick={doIgnitionStop} className="w-full sm:w-auto">
              Stop ignition
            </Btn>
          ) : null}
        </div>

        <div className="mx-auto mt-4 max-w-2xl rounded-xl border border-white/[0.08] bg-dna-bg/60 px-4 py-3 text-center text-xs text-slate-400">
          <div className="font-medium text-slate-200">{statusLine}</div>
          {cleanApi ? (
            <div className="mt-1">{enrolledHint}</div>
          ) : (
            <div className="mt-1 text-amber-400/90">Add the Face API URL under Connection below.</div>
          )}
          {apiError ? <div className="mt-1 text-rose-400/90">{apiError}</div> : null}
          <div className="mt-1 text-[11px] text-slate-500">
            Lock: {locked ? "locked" : "unlocked"} · Ignition: {ignitionOn ? "on" : "off"}
          </div>
        </div>
      </Card>
    </div>
  );
}
