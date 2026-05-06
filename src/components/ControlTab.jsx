import React, { useCallback, useEffect, useRef, useState } from "react";
import Card from "./Card";
import Btn from "./Btn";
import { ScanFace } from "lucide-react";
import { isFaceAccessAllowed } from "../utils/helpers";

const WINDOW_SIZE = 10;
const MIN_MATCHES = 6;

function evaluateWindow(history) {
  const validUsers = history.filter((h) => h.matched && h.user).map((h) => h.user);
  if (!validUsers.length) return { granted: false, candidateUser: null, candidateCount: 0 };
  const counts = {};
  for (const u of validUsers) counts[u] = (counts[u] || 0) + 1;
  let bestUser = null;
  let bestCount = 0;
  for (const [u, c] of Object.entries(counts)) {
    if (c > bestCount) {
      bestCount = c;
      bestUser = u;
    }
  }
  return { granted: bestCount >= MIN_MATCHES, candidateUser: bestUser, candidateCount: bestCount };
}

function formatFetchError(data, status, statusText) {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  if (d != null) return JSON.stringify(d);
  return `HTTP ${status} ${statusText}`;
}

export default function ControlTab({
  faceApiUrl,
  faceAccessAllowed = {},
  locked,
  ignitionOn,
  doUnlock,
  doLock,
  doIgnitionStart,
  doIgnitionStop,
  doFullReset,
  popToast,
  busy,
}) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const historyRef = useRef([]);
  const inFlightRef = useRef(false);
  const accessGrantedRef = useRef(false);
  const blockedRecognitionRef = useRef([]);
  const accessDeniedHandledRef = useRef(false);
  const intervalRef = useRef(null);
  const postUnlockIntervalRef = useRef(null);
  const doUnlockRef = useRef(doUnlock);
  const doIgnitionStartRef = useRef(doIgnitionStart);
  const stopCameraRef = useRef(null);
  const unlockOwnerRef = useRef(null);

  const [camOn, setCamOn] = useState(false);
  const [statusLine, setStatusLine] = useState("Camera off");
  const [enrolledHint, setEnrolledHint] = useState("");
  const [apiError, setApiError] = useState(null);
  const [postUnlockCountdown, setPostUnlockCountdown] = useState(null);
  const [flowStage, setFlowStage] = useState("unlock_verify");
  const [promptCountdown, setPromptCountdown] = useState(null);

  const cleanApi = (faceApiUrl || "").trim().replace(/\/$/, "");

  const stopCamera = useCallback((opts = {}) => {
    const preserveOwner = !!opts.preserveOwner;
    if (postUnlockIntervalRef.current) {
      clearInterval(postUnlockIntervalRef.current);
      postUnlockIntervalRef.current = null;
    }
    setPostUnlockCountdown(null);
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
    setCamOn(false);
    historyRef.current = [];
    blockedRecognitionRef.current = [];
    accessGrantedRef.current = false;
    accessDeniedHandledRef.current = false;
    if (!preserveOwner) unlockOwnerRef.current = null;
    setStatusLine("Camera off");
    setApiError(null);
  }, []);

  useEffect(() => {
    stopCameraRef.current = stopCamera;
  }, [stopCamera]);

  const startCamera = useCallback(async () => {
    if (postUnlockIntervalRef.current) {
      clearInterval(postUnlockIntervalRef.current);
      postUnlockIntervalRef.current = null;
    }
    setPostUnlockCountdown(null);
    accessDeniedHandledRef.current = false;
    blockedRecognitionRef.current = [];
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
      setCamOn(true);
      setApiError(null);
    } catch (e) {
      popToast("err", "Camera", e.message || "Permission denied");
    }
  }, [popToast]);

  useEffect(() => () => stopCamera(), [stopCamera]);

  useEffect(() => {
    doUnlockRef.current = doUnlock;
  }, [doUnlock]);

  useEffect(() => {
    doIgnitionStartRef.current = doIgnitionStart;
  }, [doIgnitionStart]);

  useEffect(() => {
    if (locked) unlockOwnerRef.current = null;
    if (locked) setFlowStage("unlock_verify");
  }, [locked]);

  useEffect(() => {
    if (flowStage !== "prompt" || camOn) {
      setPromptCountdown(null);
      return;
    }
    setPromptCountdown(5);
    let remain = 5;
    const t = setInterval(() => {
      remain -= 1;
      if (remain <= 0) {
        clearInterval(t);
        setPromptCountdown(0);
        void handleIgnitionPromptNo();
      } else {
        setPromptCountdown(remain);
      }
    }, 1000);
    return () => clearInterval(t);
  }, [camOn, flowStage]);

  const handleFullReset = async () => {
    const ok = await doFullReset();
    if (ok) {
      stopCameraRef.current?.();
      setFlowStage("unlock_verify");
    }
  };

  const handleIgnitionPromptNo = async () => {
    const ok = await doLock();
    if (ok) {
      stopCameraRef.current?.();
      setFlowStage("unlock_verify");
    }
  };

  const handleIgnitionPromptYes = async () => {
    setFlowStage("ignition_verify");
    await startCamera();
    setStatusLine("Ignition verify: scan the same face again.");
  };

  useEffect(() => {
    if (!cleanApi) {
      setEnrolledHint("");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${cleanApi}/api/face-status`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (cancelled) return;
        if (j.count > 0) setEnrolledHint(`Enrolled: ${j.enrolled.join(", ")}`);
        else setEnrolledHint("No users in face DB — run enroll.py in car_face_auth first.");
      } catch {
        if (!cancelled) setEnrolledHint("Cannot reach Face API (is uvicorn running?)");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cleanApi, camOn]);

  useEffect(() => {
    const scanningAllowed = flowStage === "unlock_verify" || flowStage === "ignition_verify";
    if (!camOn || !cleanApi || !scanningAllowed) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    const tick = async () => {
      if (inFlightRef.current) return;
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || video.readyState < 2) return;
      inFlightRef.current = true;
      try {
        const w = video.videoWidth;
        const h = video.videoHeight;
        if (w < 2 || h < 2) return;
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, w, h);
        const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.88));
        if (!blob) return;
        const form = new FormData();
        form.append("image", blob, "frame.jpg");
        const r = await fetch(`${cleanApi}/api/verify-frame`, { method: "POST", body: form });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          setApiError(formatFetchError(data, r.status, r.statusText));
          setStatusLine("Recognition API error");
          return;
        }
        setApiError(null);

        if (data.ok === false && data.error === "decode_failed") {
          setStatusLine("Bad frame");
          return;
        }

        const hist = historyRef.current;
        const faceCount = data.face_count ?? 0;
        if (faceCount === 0) {
          hist.push({ matched: false, user: null });
        } else if (faceCount > 1) {
          hist.push({ matched: false, user: null });
        } else {
          const allowed =
            !!data.matched && isFaceAccessAllowed(data.user, faceAccessAllowed);
          hist.push({ matched: allowed, user: data.user || null });
        }
        while (hist.length > WINDOW_SIZE) hist.shift();

        const { granted, candidateUser, candidateCount } = evaluateWindow(hist);

        const br = blockedRecognitionRef.current;
        if (faceCount === 1 && data.matched && !isFaceAccessAllowed(data.user, faceAccessAllowed)) {
          br.push({ user: data.user });
        } else {
          br.push(null);
        }
        while (br.length > WINDOW_SIZE) br.shift();
        const blockedHits = br.filter(Boolean);
        const blockedStable = blockedHits.length >= MIN_MATCHES;
        const deniedUser = blockedHits.length ? blockedHits[blockedHits.length - 1].user : null;

        if (blockedStable && !accessDeniedHandledRef.current) {
          accessDeniedHandledRef.current = true;
          popToast(
            "err",
            "Access denied",
            `${deniedUser ?? "This user"} has no access permission. Turn it on under Users → People & access.`,
          );
          stopCameraRef.current?.();
          return;
        }

        if (faceCount === 1) {
          if (data.matched && !isFaceAccessAllowed(data.user, faceAccessAllowed)) {
            setStatusLine(
              `Recognized ${data.user} (score ${data.score}) · access off — enable in Users tab · ${candidateCount}/${WINDOW_SIZE}`,
            );
          } else {
            setStatusLine(
              data.matched
                ? `Match: ${data.user} (score ${data.score}) · stable ${candidateCount}/${WINDOW_SIZE}`
                : `No match · closest ${data.user ?? "—"} (${data.score}) · ${candidateCount}/${WINDOW_SIZE}`,
            );
          }
        } else if (faceCount === 0) {
          setStatusLine("No face detected");
        } else {
          setStatusLine("Multiple faces in frame");
        }

        if (granted && !accessGrantedRef.current) {
          accessGrantedRef.current = true;
          const action =
            flowStage === "unlock_verify" ? doUnlockRef.current : ignitionOn ? null : doIgnitionStartRef.current;
          if (typeof action === "function") {
            if (flowStage === "ignition_verify") {
              const owner = unlockOwnerRef.current;
              if (!owner) {
                popToast("err", "Ignition blocked", "Unlock must be completed by face scan first.");
                setStatusLine("Ignition blocked: unlock owner missing. Re-lock and verify again.");
                stopCameraRef.current?.();
                return;
              }
              if (candidateUser && candidateUser !== owner) {
                popToast("err", "Ignition blocked", `Second verify must be the same user (${owner}).`);
                setStatusLine(`Ignition blocked: expected ${owner}, got ${candidateUser}.`);
                stopCameraRef.current?.();
                return;
              }
            }
            void action({ suppressSuccessToast: true }).then((ok) => {
              if (ok) {
                if (flowStage === "unlock_verify") {
                  unlockOwnerRef.current = candidateUser || null;
                  popToast("ok", "Device unlock", "Verified — device unlock.");
                  setFlowStage("prompt");
                }
                else if (!ignitionOn) popToast("ok", "Ignition start", "Second verify passed — ignition started.");
                else popToast("info", "Already running", "Ignition is already on.");
                setPostUnlockCountdown(3);
                let step = 0;
                if (postUnlockIntervalRef.current) {
                  clearInterval(postUnlockIntervalRef.current);
                  postUnlockIntervalRef.current = null;
                }
                postUnlockIntervalRef.current = setInterval(() => {
                  step += 1;
                  if (step >= 3) {
                    if (postUnlockIntervalRef.current) {
                      clearInterval(postUnlockIntervalRef.current);
                      postUnlockIntervalRef.current = null;
                    }
                    setPostUnlockCountdown(null);
                    stopCameraRef.current?.({ preserveOwner: true });
                  } else {
                    setPostUnlockCountdown(3 - step);
                  }
                }, 1000);
              } else {
                accessGrantedRef.current = false;
              }
            });
          } else {
            popToast("info", "Already running", "Ignition is already on.");
            stopCameraRef.current?.({ preserveOwner: true });
          }
        }
        if (!granted) accessGrantedRef.current = false;
      } catch (e) {
        setApiError(e.message);
        setStatusLine("Network error");
      } finally {
        inFlightRef.current = false;
      }
    };

    intervalRef.current = setInterval(tick, 450);
    tick();
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = null;
    };
  }, [camOn, cleanApi, faceAccessAllowed, flowStage, ignitionOn, popToast]);

  return (
    <div className="w-full pt-1">
      <Card contentClassName="p-5 sm:p-6">
          <div className="flex flex-col items-center text-center">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-violet-500/25 bg-violet-500/10">
              <ScanFace className="h-5 w-5 text-violet-400" strokeWidth={1.75} />
            </div>
            <div className="mt-4 min-w-0 max-w-lg">
              <div className="text-lg font-semibold tracking-tight text-slate-100">Face scan</div>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-400">
                First scan unlocks. Then choose Yes/No for ignition. If Yes, run a second scan with the same user to start
                ignition.
              </p>
            </div>
          </div>

          <div className="mx-auto mt-6 max-w-2xl overflow-hidden rounded-2xl border border-white/10 bg-black/40">
            <video ref={videoRef} className="aspect-video w-full object-cover" playsInline muted />
            <canvas ref={canvasRef} className="hidden" aria-hidden="true" />
          </div>

          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {!camOn ? (
              <Btn disabled={busy} onClick={startCamera}>
                {flowStage === "ignition_verify" ? "Start ignition face verify" : "Turn on camera & scan"}
              </Btn>
            ) : (
              <Btn variant="secondary" disabled={busy} onClick={stopCamera}>
                Stop camera
              </Btn>
            )}
            <Btn variant="danger" disabled={busy} onClick={handleFullReset}>
              FULL RESET
            </Btn>
            {ignitionOn ? (
              <Btn variant="secondary" disabled={busy} onClick={doIgnitionStop}>
                Stop ignition
              </Btn>
            ) : null}
          </div>

          {flowStage === "prompt" && !camOn ? (
            <div className="mx-auto mt-4 flex max-w-2xl flex-wrap items-center justify-center gap-2 rounded-xl border border-violet-500/25 bg-violet-500/10 px-4 py-3">
              <div className="w-full text-center text-sm text-violet-200">
                Start ignition now? (Choosing Yes requires a second face verify from the same user.)
              </div>
              <div className="w-full text-center text-xs text-violet-300/90">
                Auto lock in {promptCountdown ?? 5}s if no choice.
              </div>
              <Btn disabled={busy} onClick={handleIgnitionPromptYes}>
                Yes, continue
              </Btn>
              <Btn variant="secondary" disabled={busy} onClick={handleIgnitionPromptNo}>
                No, lock now
              </Btn>
            </div>
          ) : null}

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
            {postUnlockCountdown != null ? (
              <div className="mt-2 text-sm font-medium text-violet-300/95">
                Stopping camera in {postUnlockCountdown}…
              </div>
            ) : null}
          </div>
        </Card>
    </div>
  );
}
