import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Card from "./Card";
import Badge from "./Badge";
import Input from "./Input";
import Btn from "./Btn";
import { fmt, formatRelativeAgo, genId, isFaceAccessAllowed } from "../utils/helpers";
import Switch from "./Switch";

const SAMPLES_NEEDED = 10;
const AUTO_CAPTURE_MS = 500;
const MAX_AUTO_ATTEMPTS = 40;
const PI_ENROLL_FINAL_STATES = new Set(["completed", "error", "cancelled", "timeout"]);

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function sourceButtonClass(active) {
  return [
    "rounded-xl border px-4 py-3 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-50",
    active
      ? "border-violet-500/40 bg-violet-500/15 text-slate-100"
      : "border-white/[0.08] bg-dna-bg/50 text-slate-400 hover:border-white/15 hover:text-slate-200",
  ].join(" ");
}

export default function UsersTab({
  mode,
  sim,
  setSim,
  deviceUsers,
  setDeviceUsers,
  name,
  setName,
  busy,
  addUserToDirectory,
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
  const [enrollingAuto, setEnrollingAuto] = useState(false);
  const [enrollSource, setEnrollSource] = useState(mode === "device" ? "pi_camera" : "phone_camera");
  const [piEnrollStatus, setPiEnrollStatus] = useState(null);
  const enrollVideoRef = useRef(null);
  const enrollCanvasRef = useRef(null);
  const enrollStreamRef = useRef(null);
  const cancelAutoRef = useRef(false);
  const piEnrollPollRef = useRef(null);
  const createdEnrollUserRef = useRef(null);
  /** Previous Face API name list — only sync *new* names into sim (avoids re-adding after Remove when fetch returns same set with a new array ref). */
  const prevFaceNamesRef = useRef(null);

  const clearPiEnrollPoll = useCallback(() => {
    if (piEnrollPollRef.current) {
      clearInterval(piEnrollPollRef.current);
      piEnrollPollRef.current = null;
    }
  }, []);

  const cleanupCreatedEnrollUser = useCallback(async () => {
    const createdUser = createdEnrollUserRef.current;
    if (!createdUser?.id) return;
    try {
      await api.delUser(createdUser.id);
      createdEnrollUserRef.current = null;
      if (mode === "device") setDeviceUsers(await api.users());
    } catch {
      /* The original enrollment error remains the actionable message. */
    }
  }, [api, mode, setDeviceUsers]);

  const refreshLocalFaces = useCallback(async () => {
    if (!cleanApi) {
      setLocalFaceNames([]);
      return;
    }
    try {
      const r = await fetch(`${cleanApi}/api/face-status`, { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      const next = j.enrolled || [];
      setLocalFaceNames((prev) => {
        const a = [...prev].sort().join("\0");
        const b = [...next].sort().join("\0");
        if (a === b) return prev;
        return next;
      });
    } catch {
      setLocalFaceNames([]);
    }
  }, [cleanApi]);

  useEffect(() => {
    refreshLocalFaces();
  }, [refreshLocalFaces]);

  /** Same names as Access list: directory + face DB (deduped). */
  const accessUserNames = useMemo(() => {
    const fromDir = users.map((u) => u.name).filter(Boolean);
    const merged = [...new Set([...fromDir, ...localFaceNames])];
    merged.sort((a, b) => a.localeCompare(b));
    return merged;
  }, [users, localFaceNames]);

  useEffect(() => {
    if (mode !== "sim") prevFaceNamesRef.current = null;
  }, [mode]);

  useEffect(() => {
    setEnrollSource((source) => {
      if (mode === "device" || source !== "pi_camera") return source;
      return "phone_camera";
    });
  }, [mode]);

  /**
   * Sim: add to sim.users only names that *newly appear* in Face API (vs previous fetch).
   * Never "fill gap" when sim has fewer rows than API — that was re-adding removed users and prepending them (looked like row jumping).
   */
  useEffect(() => {
    if (mode !== "sim" || typeof setSim !== "function") return;
    const curr = localFaceNames.filter(Boolean);
    const prev = prevFaceNamesRef.current;

    if (prev === null) {
      prevFaceNamesRef.current = [...curr];
      if (curr.length === 0) return;
      setSim((s) => {
        const existing = new Set(s.users.map((u) => u.name));
        const toAdd = curr.filter((n) => !existing.has(n));
        if (toAdd.length === 0) return s;
        return {
          ...s,
          users: [
            ...s.users,
            ...toAdd.map((userName) => ({ id: genId("u"), name: userName, createdAt: Date.now() })),
          ],
        };
      });
      return;
    }

    const added = curr.filter((n) => !prev.includes(n));
    prevFaceNamesRef.current = [...curr];
    if (added.length === 0) return;

    setSim((s) => {
      const existing = new Set(s.users.map((u) => u.name));
      const toAdd = added.filter((n) => !existing.has(n));
      if (toAdd.length === 0) return s;
      return {
        ...s,
        users: [
          ...s.users,
          ...toAdd.map((userName) => ({ id: genId("u"), name: userName, createdAt: Date.now() })),
        ],
      };
    });
  }, [mode, localFaceNames, setSim]);

  const handleRemoveUser = async (id) => {
    await delUser(id);
    await refreshLocalFaces();
  };

  /** Directory row: full remove via delUser. Face-only name (no sim/device row): strip template + access prefs. */
  const removePersonByName = async (displayName) => {
    const n = String(displayName || "").trim();
    if (!n) return;
    const u = users.find((x) => x.name === n);
    if (u) {
      await handleRemoveUser(u.id);
      return;
    }
    if (!confirm(`Remove "${n}" from the face database and access list?`)) return;
    try {
      if (cleanApi) {
        await fetch(`${cleanApi}/api/face/remove`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: n }),
        });
      }
      if (mode !== "device") {
        setFaceAccessAllowed((prev) => {
          const next = { ...prev };
          delete next[n];
          return next;
        });
      }
      await refreshLocalFaces();
      popToast("ok", "Removed", `${n} cleared from face DB.`);
    } catch (e) {
      popToast("err", "Remove failed", e.message || String(e));
    }
  };

  useEffect(
    () => () => {
      cancelAutoRef.current = true;
      clearPiEnrollPoll();
      if (enrollStreamRef.current) {
        enrollStreamRef.current.getTracks().forEach((t) => t.stop());
        enrollStreamRef.current = null;
      }
    },
    [clearPiEnrollPoll],
  );

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
        const video = enrollVideoRef.current;
        const ready = new Promise((res) => video.addEventListener("loadedmetadata", res, { once: true }));
        video.srcObject = stream;
        await ready;
        await video.play().catch((e) => {
          if (e.name !== "AbortError") throw e;
        });
      }
      setEnrollCamOn(true);
      return true;
    } catch (e) {
      popToast("err", "Camera", e.message || "Permission denied");
      return false;
    }
  }, [popToast]);

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

  const grabFrameBlob = async () => {
    const video = enrollVideoRef.current;
    const canvas = enrollCanvasRef.current;
    if (!video || !canvas || video.readyState < 2) throw new Error("Video not ready");
    const w = video.videoWidth;
    const h = video.videoHeight;
    if (w < 2 || h < 2) throw new Error("Video not ready");
    canvas.width = w;
    canvas.height = h;
    canvas.getContext("2d").drawImage(video, 0, 0, w, h);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.9));
    if (!blob) throw new Error("Could not encode frame");
    return blob;
  };

  const postSample = async (sessionId, blob) => {
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("image", blob, "sample.jpg");
    const r = await fetch(`${cleanApi}/api/enroll/sample`, { method: "POST", body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
    return data;
  };

  const finishEnrollWithId = async (sessionId) => {
    const r = await fetch(`${cleanApi}/api/enroll/finish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    popToast("ok", "Face enrolled", `${data.user} — ${data.samples} samples saved to local DB.`);
    setFaceAccessAllowed((prev) => ({ ...prev, [data.user]: true }));
  };

  const resetEnrollUi = () => {
    clearPiEnrollPoll();
    setEnrollSession(null);
    setEnrollCount(0);
    setPiEnrollStatus(null);
    stopEnrollCamera();
    setEnrollingAuto(false);
  };

  const finishPiEnroll = async (status, displayName) => {
    clearPiEnrollPoll();
    const state = status?.state || "error";
    if (state === "completed") {
      createdEnrollUserRef.current = null;
      if (status?.recognition_available === false) {
        popToast("info", "Enrollment simulated", `${displayName} was added, but this fake does not store a recognition template.`);
      } else {
        popToast("ok", "Face enrolled", `${displayName} enrolled from Pi camera.`);
      }
      setFaceAccessAllowed((prev) => ({ ...prev, [displayName]: true }));
      setName("");
      await refreshLocalFaces();
    } else if (state === "cancelled") {
      await cleanupCreatedEnrollUser();
      popToast("info", "Cancelled", "Pi camera enrollment stopped.");
    } else {
      await cleanupCreatedEnrollUser();
      popToast("err", "Enrollment failed", status?.message || "Pi camera enrollment did not complete.");
    }
    resetEnrollUi();
  };

  const runPiCameraEnroll = async (displayName) => {
    if (mode !== "device") {
      popToast("err", "Device API", "Turn on Device API mode and point Base URL at the Pi or Fake Pi API.");
      return;
    }

    cancelAutoRef.current = false;
    setEnrollingAuto(true);
    setEnrollCount(0);
    setPiEnrollStatus({ state: "starting", count: 0, samples_needed: SAMPLES_NEEDED, source: "pi_camera" });

    try {
      createdEnrollUserRef.current = await addUserToDirectory(displayName);
    } catch (e) {
      popToast("err", "Add user failed", e.message);
      resetEnrollUi();
      return;
    }

    let sessionId;
    try {
      const start = await api.piEnrollStart({ name: displayName, source: "pi_camera" });
      sessionId = start.session_id || start.sessionId;
      const next = {
        ...start,
        state: start.state || "capturing",
        session_id: sessionId,
        samples_needed: start.samples_needed || SAMPLES_NEEDED,
      };
      setPiEnrollStatus(next);
      setEnrollCount(next.count || 0);

      if (PI_ENROLL_FINAL_STATES.has(next.state)) {
        await finishPiEnroll(next, displayName);
        return;
      }
    } catch (e) {
      await cleanupCreatedEnrollUser();
      popToast("err", "Enrollment start failed", e.message);
      resetEnrollUi();
      return;
    }

    if (!sessionId) {
      await finishPiEnroll({ state: "error", message: "Pi did not return an enrollment session." }, displayName);
      return;
    }

    piEnrollPollRef.current = setInterval(async () => {
      try {
        const raw = await api.piEnrollStatus(sessionId);
        const next = {
          ...raw,
          state: raw.state || "capturing",
          session_id: raw.session_id || raw.sessionId || sessionId,
          samples_needed: raw.samples_needed || SAMPLES_NEEDED,
        };
        setPiEnrollStatus(next);
        setEnrollCount(next.count || 0);
        if (PI_ENROLL_FINAL_STATES.has(next.state)) {
          await finishPiEnroll(next, displayName);
        }
      } catch (e) {
        await finishPiEnroll({ state: "error", message: e.message }, displayName);
      }
    }, 1000);
  };

  const runAddAndEnroll = async () => {
    const n = name.trim();
    if (!n) return popToast("err", "Name required", "Enter a display name.");
    if (enrollingAuto || busy) return;
    if (enrollSource === "pi_camera") {
      await runPiCameraEnroll(n);
      return;
    }
    if (!cleanApi) return popToast("err", "Face API", "Set Face API URL under Control → Connection.");

    cancelAutoRef.current = false;
    setEnrollingAuto(true);
    setEnrollCount(0);

    try {
      createdEnrollUserRef.current = await addUserToDirectory(n);
    } catch (e) {
      popToast("err", "Add user failed", e.message);
      setEnrollingAuto(false);
      return;
    }

    if (cancelAutoRef.current) {
      await cleanupCreatedEnrollUser();
      setEnrollingAuto(false);
      popToast("info", "Cancelled", "Enrollment stopped.");
      return;
    }

    if (enrollSession) await cancelRemoteSession(enrollSession);

    let sessionId;
    try {
      const r = await fetch(`${cleanApi}/api/enroll/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: n }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
      sessionId = data.session_id;
      setEnrollSession(sessionId);
    } catch (e) {
      await cleanupCreatedEnrollUser();
      popToast("err", "Enrollment start failed", e.message);
      setEnrollingAuto(false);
      return;
    }

    if (cancelAutoRef.current) {
      await cancelRemoteSession(sessionId);
      await cleanupCreatedEnrollUser();
      resetEnrollUi();
      popToast("info", "Cancelled", "Enrollment stopped.");
      return;
    }

    const camOk = await startEnrollCamera();
    if (!camOk) {
      await cancelRemoteSession(sessionId);
      await cleanupCreatedEnrollUser();
      resetEnrollUi();
      return;
    }

    const video = enrollVideoRef.current;
    let waited = 0;
    while (video && video.readyState < 2 && waited < 60) {
      await sleep(50);
      waited++;
    }

    let count = 0;
    let attempts = 0;
    try {
      while (count < SAMPLES_NEEDED && attempts < MAX_AUTO_ATTEMPTS && !cancelAutoRef.current) {
        if (attempts > 0) await sleep(AUTO_CAPTURE_MS);
        if (cancelAutoRef.current) break;
        attempts++;
        const blob = await grabFrameBlob();
        const data = await postSample(sessionId, blob);
        count = data.count ?? count;
        setEnrollCount(count);
      }
    } catch (e) {
      popToast("err", "Capture failed", e.message);
      await cancelRemoteSession(sessionId);
      await cleanupCreatedEnrollUser();
      resetEnrollUi();
      return;
    }

    if (cancelAutoRef.current) {
      await cancelRemoteSession(sessionId);
      await cleanupCreatedEnrollUser();
      resetEnrollUi();
      popToast("info", "Cancelled", "Enrollment stopped.");
      return;
    }

    if (count < SAMPLES_NEEDED) {
      popToast(
        "err",
        "Enrollment incomplete",
        `Got ${count}/${SAMPLES_NEEDED} valid samples. Keep one face in frame and try again.`,
      );
      await cancelRemoteSession(sessionId);
      await cleanupCreatedEnrollUser();
      resetEnrollUi();
      return;
    }

    try {
      await finishEnrollWithId(sessionId);
      createdEnrollUserRef.current = null;
      setName("");
      await refreshLocalFaces();
    } catch (e) {
      await cancelRemoteSession(sessionId);
      await cleanupCreatedEnrollUser();
      popToast("err", "Finish failed", e.message);
    } finally {
      resetEnrollUi();
    }
  };

  const onCancelEnroll = async () => {
    cancelAutoRef.current = true;
    if (enrollSource === "pi_camera") {
      const sessionId = piEnrollStatus?.session_id || piEnrollStatus?.sessionId;
      clearPiEnrollPoll();
      if (sessionId) {
        try {
          await api.piEnrollCancel(sessionId);
        } catch {
          /* ignore cancel failures */
        }
      }
      await cleanupCreatedEnrollUser();
      resetEnrollUi();
      popToast("info", "Cancelled", "Pi camera enrollment stopped.");
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-3">
      <Card>
        <div className="text-sm font-semibold text-slate-100">Checklist</div>
        <div className="mt-3 space-y-2 text-sm text-slate-400">
          <div className="flex gap-2">
            <span className="text-violet-400">▸</span> Even lighting, one face in frame
          </div>
          <div className="flex gap-2">
            <span className="text-violet-400">▸</span> Hold still ~{(SAMPLES_NEEDED * AUTO_CAPTURE_MS) / 1000}s while samples are taken
          </div>
          <div className="flex gap-2">
            <span className="text-fuchsia-400">▸</span> Samples fire every {AUTO_CAPTURE_MS / 1000}s until {SAMPLES_NEEDED} are accepted
          </div>
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-slate-100">Face templates (InsightFace)</div>
            <div className="mt-1 text-xs text-slate-400">
              Same database as <span className="font-mono text-[11px]">enroll.py</span>. Adds the person to{" "}
              <span className="text-slate-300">People &amp; Access</span> below, then captures {SAMPLES_NEEDED} samples every{" "}
              {AUTO_CAPTURE_MS / 1000}s automatically.
            </div>
          </div>
          <Badge>{localFaceNames.length} in local DB</Badge>
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            aria-pressed={enrollSource === "pi_camera"}
            className={sourceButtonClass(enrollSource === "pi_camera")}
            disabled={enrollingAuto}
            onClick={() => setEnrollSource("pi_camera")}
          >
            <div className="font-medium">Pi camera</div>
            <div className="mt-1 text-xs text-slate-500">Best match for unlock scans.</div>
          </button>
          <button
            type="button"
            aria-pressed={enrollSource === "phone_camera"}
            className={sourceButtonClass(enrollSource === "phone_camera")}
            disabled={enrollingAuto}
            onClick={() => setEnrollSource("phone_camera")}
          >
            <div className="font-medium">This device camera</div>
            <div className="mt-1 text-xs text-slate-500">Uses the Face API upload flow.</div>
          </button>
        </div>

        {enrollSource === "phone_camera" && !cleanApi ? (
          <div className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200/90">
            Set <span className="font-medium">Face API</span> under Control → Connection to use this device camera.
          </div>
        ) : null}

        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <label htmlFor="display-name" className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">Display name</label>
            <Input id="display-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name" disabled={enrollingAuto} />
          </div>
          <Btn
            disabled={busy || enrollingAuto || (enrollSource === "phone_camera" && !cleanApi)}
            onClick={runAddAndEnroll}
            className="shrink-0 sm:min-w-[11rem]"
          >
            Add & enroll face
          </Btn>
        </div>

        {enrollingAuto ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge>
              {enrollSource === "pi_camera" ? "Pi capture" : "Auto-capture"} {enrollCount} / {SAMPLES_NEEDED}
            </Badge>
            <Btn variant="secondary" type="button" onClick={onCancelEnroll}>
              Cancel
            </Btn>
          </div>
        ) : null}

        {enrollSource === "pi_camera" ? (
          <div className="mt-4 rounded-xl border border-white/[0.08] bg-dna-bg/60 px-4 py-3 text-center text-xs text-slate-400">
            <div className="font-medium text-slate-200">
              {piEnrollStatus?.state === "starting"
                ? "Starting Pi camera enrollment."
                : piEnrollStatus?.state === "capturing"
                ? "Pi camera is collecting samples."
                : "Pi camera will collect samples on the device."}
            </div>
            {mode !== "device" ? (
              <div className="mt-1 text-amber-400/90">Turn on Device API mode to use Pi camera enrollment.</div>
            ) : null}
          </div>
        ) : (
          <>
            <div className="mt-4 overflow-hidden rounded-xl border border-white/10 bg-black/40">
              <video ref={enrollVideoRef} className="aspect-video w-full object-cover" playsInline muted />
              <canvas ref={enrollCanvasRef} className="hidden" aria-hidden="true" />
            </div>

            {!enrollingAuto && !enrollCamOn ? (
              <p className="mt-2 text-center text-xs text-slate-500">Camera turns on when you start Add & enroll face.</p>
            ) : null}
          </>
        )}

        {localFaceNames.length > 0 ? (
          <div className="mt-4 text-xs text-slate-500">
            Local DB: <span className="text-slate-400">{localFaceNames.join(", ")}</span>
          </div>
        ) : null}
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-slate-100">People &amp; Access</div>
            <div className="mt-1 text-xs text-slate-400">
              {mode === "device" ? (
                "Pi directory plus Face API templates. Remove clears directory and face data when possible. Toggle controls who may unlock from face scan."
              ) : (
                <>
                  Sim directory plus face DB. Remove drops the sim row and clears the template. Access toggles are stored in this browser (
                  <span className="font-mono text-[11px]">localStorage</span>
                  ).
                </>
              )}
            </div>
          </div>
          <Badge>{accessUserNames.length}</Badge>
        </div>

        <div className="mt-4 space-y-2">
          {mode !== "device" && !cleanApi ? (
            <div className="text-sm text-slate-500">Set Face API under Control → Connection to sync the roster with the face DB.</div>
          ) : accessUserNames.length === 0 ? (
            <div className="text-sm text-slate-500">No people yet — use Face templates above to add &amp; enroll.</div>
          ) : (
            accessUserNames.map((n) => {
              const u = users.find((x) => x.name === n);
              const hasFace = localFaceNames.includes(n);
              const extraMeta =
                !hasFace ? "No face template yet" : !u ? "Face DB only (no directory row)" : null;
              return (
                <div
                  key={n}
                  className="flex flex-col gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-slate-100">{n}</div>
                    <div className="text-xs text-slate-500">
                      {u ? (
                        <>
                          <span
                            title={fmt(u.createdAt)}
                            className="cursor-help underline decoration-slate-500/45 decoration-dotted underline-offset-2"
                          >
                            Added {formatRelativeAgo(u.createdAt)}
                          </span>
                          {extraMeta ? ` · ${extraMeta}` : ""}
                        </>
                      ) : (
                        extraMeta
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-3">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] uppercase tracking-wide text-slate-500">
                        {isFaceAccessAllowed(n, faceAccessAllowed) ? "allowed" : "blocked"}
                      </span>
                      <Switch
                        ariaLabel={`${n} face access`}
                        checked={isFaceAccessAllowed(n, faceAccessAllowed)}
                        onChange={async (allowed) => {
                          if (mode === "sim" && typeof setSim === "function") {
                            setFaceAccessAllowed((prev) => ({ ...prev, [n]: allowed }));
                            setSim((s) => ({
                              ...s,
                              logs: [
                                {
                                  id: genId("log"),
                                  ts: Date.now(),
                                  type: "access_change",
                                  ok: true,
                                  detail: allowed ? `Access allowed for ${n}` : `Access blocked for ${n}`,
                                },
                                ...s.logs,
                              ].slice(0, 80),
                            }));
                          }
                          if (mode === "device") {
                            const row = users.find((x) => x.name === n);
                            if (row) {
                              try {
                                await api.setAccess(row.id, allowed);
                                if (typeof setDeviceUsers === "function") {
                                  setDeviceUsers((prev) =>
                                    prev.map((u) => (u.id === row.id ? { ...u, faceAccess: allowed } : u)),
                                  );
                                }
                              } catch {
                                /* non-fatal */
                              }
                            }
                          }
                        }}
                      />
                    </div>
                    <Btn variant="danger" disabled={busy || enrollingAuto} onClick={() => removePersonByName(n)}>
                      Remove
                    </Btn>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </Card>
    </div>
  );
}
