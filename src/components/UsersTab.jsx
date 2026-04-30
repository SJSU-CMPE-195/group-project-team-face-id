import React, { useCallback, useEffect, useRef, useState } from "react";
import Card from "./Card";
import Badge from "./Badge";
import Input from "./Input";
import Btn from "./Btn";
import { fmt, isFaceAccessAllowed } from "../utils/helpers";
import Switch from "./Switch";

const SAMPLES_NEEDED = 10;

export default function UsersTab({
  mode,
  sim,
  deviceUsers,
  name,
  setName,
  busy,
  addUser,
  delUser,
  faceApiUrl,
  popToast,
  faceAccessAllowed = {},
  setFaceAccessAllowed,
  api,
}) {
  const users = mode === "device" ? deviceUsers : sim.users;
  const cleanApi = (faceApiUrl || "").trim().replace(/\/$/, "");

  const [localFaceNames, setLocalFaceNames] = useState([]);
  const [enrollSession, setEnrollSession] = useState(null);
  const [enrollCount, setEnrollCount] = useState(0);
  const [enrollCamOn, setEnrollCamOn] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const enrollVideoRef = useRef(null);
  const enrollCanvasRef = useRef(null);
  const enrollStreamRef = useRef(null);

  const refreshLocalFaces = useCallback(async () => {
    if (!cleanApi) {
      setLocalFaceNames([]);
      return;
    }
    try {
      const r = await fetch(`${cleanApi}/api/face-status`);
      if (!r.ok) return;
      const j = await r.json();
      setLocalFaceNames(j.enrolled || []);
    } catch {
      setLocalFaceNames([]);
    }
  }, [cleanApi]);

  useEffect(() => {
    refreshLocalFaces();
  }, [refreshLocalFaces]);

  const stopEnrollCamera = useCallback(() => {
    if (enrollStreamRef.current) {
      enrollStreamRef.current.getTracks().forEach((t) => t.stop());
      enrollStreamRef.current = null;
    }
    if (enrollVideoRef.current) enrollVideoRef.current.srcObject = null;
    setEnrollCamOn(false);
  }, []);

  const startEnrollCamera = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      });
      enrollStreamRef.current = stream;
      if (enrollVideoRef.current) {
        enrollVideoRef.current.srcObject = stream;
        await enrollVideoRef.current.play();
      }
      setEnrollCamOn(true);
    } catch (e) {
      popToast("err", "Camera", e.message || "Permission denied");
    }
  }, [popToast]);

  useEffect(() => () => stopEnrollCamera(), [stopEnrollCamera]);

  const cancelRemoteSession = async (sid) => {
    if (!cleanApi || !sid) return;
    try {
      await fetch(`${cleanApi}/api/enroll/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      });
    } catch {
      /* ignore */
    }
  };

  const startEnrollSession = async () => {
    const n = name.trim();
    if (!n) return popToast("err", "Name required", "Enter a display name in the field above.");
    if (!cleanApi) return popToast("err", "Face API", "Set Face API URL under Control → Connection.");
    if (enrollSession) await cancelRemoteSession(enrollSession);
    try {
      const r = await fetch(`${cleanApi}/api/enroll/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: n }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
      setEnrollSession(data.session_id);
      setEnrollCount(0);
      popToast("ok", "Enrollment started", `Add ${SAMPLES_NEEDED} samples with the button below.`);
    } catch (e) {
      popToast("err", "Start failed", e.message);
    }
  };

  const captureSample = async () => {
    if (!enrollSession || !cleanApi) return;
    const video = enrollVideoRef.current;
    const canvas = enrollCanvasRef.current;
    if (!video || !canvas || video.readyState < 2) {
      return popToast("err", "Camera", "Turn on the camera and wait for preview.");
    }
    setCapturing(true);
    try {
      const w = video.videoWidth;
      const h = video.videoHeight;
      if (w < 2 || h < 2) throw new Error("Video not ready");
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d").drawImage(video, 0, 0, w, h);
      const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.9));
      if (!blob) throw new Error("Could not encode frame");
      const form = new FormData();
      form.append("session_id", enrollSession);
      form.append("image", blob, "sample.jpg");
      const r = await fetch(`${cleanApi}/api/enroll/sample`, { method: "POST", body: form });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
      setEnrollCount(data.count ?? 0);
      if (!data.ok) {
        if (data.message === "no_face") popToast("info", "No face", "Center one face and try again.");
        else popToast("info", "Multiple faces", "Only one person in frame.");
      } else {
        popToast("ok", "Sample saved", `${data.count} / ${SAMPLES_NEEDED}`);
      }
    } catch (e) {
      popToast("err", "Capture failed", e.message);
    } finally {
      setCapturing(false);
    }
  };

  const finishEnroll = async () => {
    if (!enrollSession || !cleanApi) return;
    try {
      const r = await fetch(`${cleanApi}/api/enroll/finish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: enrollSession }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
      popToast("ok", "Face enrolled", `${data.user} — ${data.samples} samples saved to local DB.`);
      setFaceAccessAllowed((prev) => ({ ...prev, [data.user]: true }));
      setEnrollSession(null);
      setEnrollCount(0);
      stopEnrollCamera();
      await refreshLocalFaces();
    } catch (e) {
      popToast("err", "Finish failed", e.message);
    }
  };

  const cancelEnroll = async () => {
    await cancelRemoteSession(enrollSession);
    setEnrollSession(null);
    setEnrollCount(0);
    stopEnrollCamera();
  };

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <div className="space-y-3 md:col-span-2">
        <Card>
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-slate-100">Enrolled users</div>
              <div className="mt-1 text-xs text-slate-400">
                {mode === "device"
                  ? "Names on Pi SQLite; face templates from your enrollment pipeline."
                  : "Simulated templates for the demo."}
              </div>
            </div>
            <Badge>{users.length} users</Badge>
          </div>

          <div className="mt-4 flex gap-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name" />
            <Btn disabled={busy} onClick={addUser}>
              Add
            </Btn>
          </div>

          <div className="mt-4 space-y-2">
            {users.length === 0 ? (
              <div className="text-sm text-slate-500">No users yet.</div>
            ) : (
              users.map((u) => (
                <div
                  key={u.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3"
                >
                  <div>
                    <div className="text-sm font-medium text-slate-100">{u.name}</div>
                    <div className="text-xs text-slate-500">Added {fmt(u.createdAt)}</div>
                  </div>
                  <Btn variant="danger" disabled={busy} onClick={() => delUser(u.id)}>
                    Remove
                  </Btn>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-slate-100">Face templates (InsightFace)</div>
              <div className="mt-1 text-xs text-slate-400">
                Same database as <span className="font-mono text-[11px]">enroll.py</span> — use your browser camera here.
                Uses the <span className="text-slate-300">Display name</span> above as the label.
              </div>
            </div>
            <Badge>{localFaceNames.length} in local DB</Badge>
          </div>

          {!cleanApi ? (
            <div className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200/90">
              Set <span className="font-medium">Face API</span> under Control → Connection to enable browser enrollment.
            </div>
          ) : (
            <>
              <div className="mt-4 overflow-hidden rounded-xl border border-white/10 bg-black/40">
                <video ref={enrollVideoRef} className="aspect-video w-full object-cover" playsInline muted />
                <canvas ref={enrollCanvasRef} className="hidden" aria-hidden="true" />
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                {!enrollSession ? (
                  <Btn disabled={busy} onClick={startEnrollSession}>
                    Start face enrollment
                  </Btn>
                ) : (
                  <>
                    <Badge>{enrollCount} / {SAMPLES_NEEDED} samples</Badge>
                    <Btn disabled={busy || capturing || !enrollCamOn} onClick={captureSample}>
                      {capturing ? "Saving…" : "Capture sample"}
                    </Btn>
                    <Btn
                      disabled={busy || enrollCount < SAMPLES_NEEDED}
                      variant="blue"
                      onClick={finishEnroll}
                    >
                      Save to face database
                    </Btn>
                    <Btn variant="secondary" disabled={busy} onClick={cancelEnroll}>
                      Cancel
                    </Btn>
                  </>
                )}
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                {!enrollCamOn ? (
                  <Btn variant="secondary" disabled={busy} onClick={startEnrollCamera}>
                    Turn on camera
                  </Btn>
                ) : (
                  <Btn variant="secondary" disabled={busy} onClick={stopEnrollCamera}>
                    Turn off camera
                  </Btn>
                )}
              </div>

              {localFaceNames.length > 0 ? (
                <div className="mt-4 text-xs text-slate-500">
                  Local DB:{" "}
                  <span className="text-slate-400">{localFaceNames.join(", ")}</span>
                </div>
              ) : null}
            </>
          )}
        </Card>
      </div>

      <div className="flex flex-col gap-3">
        <Card>
          <div className="text-sm font-semibold text-slate-100">Checklist</div>
          <div className="mt-3 space-y-2 text-sm text-slate-400">
            <div className="flex gap-2">
              <span className="text-violet-400">▸</span> Even lighting
            </div>
            <div className="flex gap-2">
              <span className="text-violet-400">▸</span> {SAMPLES_NEEDED} samples, slight angle changes
            </div>
            <div className="flex gap-2">
              <span className="text-fuchsia-400">▸</span> One face in frame per capture
            </div>
          </div>
        </Card>

        <Card>
          <div className="text-sm font-semibold text-slate-100">Access authorization</div>
          <div className="mt-1 text-xs text-slate-400">
            Only users with access <span className="text-slate-300">on</span> count toward unlock on the Control face scan. Stored in this browser (
            <span className="font-mono text-[11px]">localStorage</span>).
          </div>
          <div className="mt-4 space-y-2">
            {!cleanApi ? (
              <div className="text-sm text-slate-500">Set Face API to list local face templates.</div>
            ) : localFaceNames.length === 0 ? (
              <div className="text-sm text-slate-500">No faces in local DB yet.</div>
            ) : (
              localFaceNames.map((n) => (
                <div
                  key={n}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-3 py-2.5"
                >
                  <span className="truncate text-sm font-medium text-slate-100">{n}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-[11px] uppercase tracking-wide text-slate-500">
                      {isFaceAccessAllowed(n, faceAccessAllowed) ? "allowed" : "blocked"}
                    </span>
                    <Switch
                      checked={isFaceAccessAllowed(n, faceAccessAllowed)}
                      onChange={async (allowed) => {
                        setFaceAccessAllowed((prev) => ({ ...prev, [n]: allowed }));
                        if (mode === "device") {
                          const user = users.find((u) => u.name === n);
                          if (user) {
                            try { await api.setAccess(user.id, allowed); } catch { /* non-fatal */ }
                          }
                        }
                      }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
