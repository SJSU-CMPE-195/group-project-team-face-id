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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export default function UsersTab({
  mode,
  sim,
  setSim,
  deviceUsers,
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
  const enrollVideoRef = useRef(null);
  const enrollCanvasRef = useRef(null);
  const enrollStreamRef = useRef(null);
  const cancelAutoRef = useRef(false);
  /** Previous Face API name list — only sync *new* names into sim (avoids re-adding after Remove when fetch returns same set with a new array ref). */
  const prevFaceNamesRef = useRef(null);

  const refreshLocalFaces = useCallback(async () => {
    if (!cleanApi) {
      setLocalFaceNames([]);
      return;
    }
    try {
      const r = await fetch(`${cleanApi}/api/face-status`);
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
      if (enrollStreamRef.current) {
        enrollStreamRef.current.getTracks().forEach((t) => t.stop());
        enrollStreamRef.current = null;
      }
    },
    [],
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
    setEnrollSession(null);
    setEnrollCount(0);
    stopEnrollCamera();
    setEnrollingAuto(false);
  };

  const runAddAndEnroll = async () => {
    const n = name.trim();
    if (!n) return popToast("err", "Name required", "Enter a display name.");
    if (!cleanApi) return popToast("err", "Face API", "Set Face API URL under Control → Connection.");
    if (enrollingAuto || busy) return;

    cancelAutoRef.current = false;
    setEnrollingAuto(true);
    setEnrollCount(0);

    try {
      await addUserToDirectory(n);
    } catch (e) {
      popToast("err", "Add user failed", e.message);
      setEnrollingAuto(false);
      return;
    }

    if (cancelAutoRef.current) {
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
      popToast("err", "Enrollment start failed", e.message);
      setEnrollingAuto(false);
      return;
    }

    if (cancelAutoRef.current) {
      await cancelRemoteSession(sessionId);
      resetEnrollUi();
      popToast("info", "Cancelled", "Enrollment stopped.");
      return;
    }

    const camOk = await startEnrollCamera();
    if (!camOk) {
      await cancelRemoteSession(sessionId);
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
      resetEnrollUi();
      return;
    }

    if (cancelAutoRef.current) {
      await cancelRemoteSession(sessionId);
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
      resetEnrollUi();
      return;
    }

    try {
      await finishEnrollWithId(sessionId);
      setName("");
      await refreshLocalFaces();
    } catch (e) {
      popToast("err", "Finish failed", e.message);
    } finally {
      resetEnrollUi();
    }
  };

  const onCancelEnroll = () => {
    cancelAutoRef.current = true;
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

        {!cleanApi ? (
          <div className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200/90">
            Set <span className="font-medium">Face API</span> under Control → Connection to enable enrollment.
          </div>
        ) : (
          <>
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1">
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">Display name</label>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name" disabled={enrollingAuto} />
              </div>
              <Btn disabled={busy || enrollingAuto} onClick={runAddAndEnroll} className="shrink-0 sm:min-w-[11rem]">
                Add & enroll face
              </Btn>
            </div>

            {enrollingAuto ? (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Badge>
                  Auto-capture {enrollCount} / {SAMPLES_NEEDED}
                </Badge>
                <Btn variant="secondary" type="button" onClick={onCancelEnroll}>
                  Cancel
                </Btn>
              </div>
            ) : null}

            <div className="mt-4 overflow-hidden rounded-xl border border-white/10 bg-black/40">
              <video ref={enrollVideoRef} className="aspect-video w-full object-cover" playsInline muted />
              <canvas ref={enrollCanvasRef} className="hidden" aria-hidden="true" />
            </div>

            {!enrollingAuto && !enrollCamOn ? (
              <p className="mt-2 text-center text-xs text-slate-500">Camera turns on when you start Add & enroll face.</p>
            ) : null}

            {localFaceNames.length > 0 ? (
              <div className="mt-4 text-xs text-slate-500">
                Local DB: <span className="text-slate-400">{localFaceNames.join(", ")}</span>
              </div>
            ) : null}
          </>
        )}
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
          {!cleanApi ? (
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
                        checked={isFaceAccessAllowed(n, faceAccessAllowed)}
                        onChange={async (allowed) => {
                          setFaceAccessAllowed((prev) => ({ ...prev, [n]: allowed }));
                          if (mode === "sim" && typeof setSim === "function") {
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
