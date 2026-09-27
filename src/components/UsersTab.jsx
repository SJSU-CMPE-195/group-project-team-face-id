import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Card from "./Card";
import Badge from "./Badge";
import Input from "./Input";
import Btn from "./Btn";
import { genId, isFaceAccessAllowed } from "../utils/helpers";
import Switch from "./Switch";
import useOperationGrant from "../security/useOperationGrant.js";
import useSecurity from "../security/useSecurity.js";
import PairingInviteCard from "./PairingInviteCard.jsx";

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
  currentUser,
  devices,
  setDevices,
}) {
  const { requestGrant } = useOperationGrant();
  const { expireSession } = useSecurity();
  const allUsers = mode === "device" ? deviceUsers : sim.users;
  const users = currentUser.is_admin
    ? allUsers
    : allUsers.filter((user) => user.id === currentUser.id);
  const cleanApi = (faceApiUrl || "").trim().replace(/\/$/, "");

  const [localFaceNames, setLocalFaceNames] = useState([]);
  const [enrollCount, setEnrollCount] = useState(0);
  const [, setEnrollCamOn] = useState(false);
  const [enrollingAuto, setEnrollingAuto] = useState(false);
  const [enrollSource, setEnrollSource] = useState(
    mode === "device" ? "device_camera" : "phone_camera",
  );
  const [newUserPin, setNewUserPin] = useState("");
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false);
  const [pinDrafts, setPinDrafts] = useState({});
  const [invite, setInvite] = useState(null);
  const [inviteBusy, setInviteBusy] = useState(false);
  const invitePendingRef = useRef(false);
  const enrollVideoRef = useRef(null);
  const enrollCanvasRef = useRef(null);
  const enrollStreamRef = useRef(null);
  const piEnrollPollRef = useRef(null);
  const activeEnrollRef = useRef(null);
  const enrollGenerationRef = useRef(0);
  const pendingEnrollCancelRef = useRef(null);
  /** Previous Face API name list — only sync *new* names into sim (avoids re-adding after Remove when fetch returns same set with a new array ref). */
  const prevFaceNamesRef = useRef(null);

  const clearPiEnrollPoll = useCallback(() => {
    if (piEnrollPollRef.current) {
      clearTimeout(piEnrollPollRef.current);
      piEnrollPollRef.current = null;
    }
  }, []);

  const refreshLocalFaces = useCallback(async () => {
    try {
      let j;
      if (mode === "device") {
        j = await api.faceStatus();
      } else {
        if (!cleanApi) {
          setLocalFaceNames([]);
          return;
        }
        const r = await fetch(`${cleanApi}/api/face-status`, { cache: "no-store" });
        if (!r.ok) return;
        j = await r.json();
      }
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
  }, [api, cleanApi, mode]);

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
      if (mode === "device" || source !== "device_camera") return source;
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
      if (u.is_owner) {
        popToast("err", "Owner protected", "Transfer ownership before removing the owner account.");
        return;
      }
      await handleRemoveUser(u.id);
      return;
    }
    if (mode === "device") {
      popToast(
        "info",
        "Refresh users",
        "Refresh the host's user list before removing this person.",
      );
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
      enrollGenerationRef.current += 1;
      pendingEnrollCancelRef.current = null;
      clearPiEnrollPoll();
      const activeEnrollment = activeEnrollRef.current;
      activeEnrollRef.current = null;
      if (activeEnrollment?.cancel) {
        void activeEnrollment.cancel().catch(() => {});
      }
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

  const startEnrollCamera = useCallback(async (generation) => {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      });
      if (generation !== enrollGenerationRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return false;
      }
      enrollStreamRef.current = stream;
      if (enrollVideoRef.current) {
        const video = enrollVideoRef.current;
        const ready = new Promise((res) => video.addEventListener("loadedmetadata", res, { once: true }));
        video.srcObject = stream;
        await ready;
        await video.play().catch((e) => {
          if (e.name !== "AbortError") throw e;
        });
        if (generation !== enrollGenerationRef.current) {
          stream.getTracks().forEach((track) => track.stop());
          if (enrollStreamRef.current === stream) enrollStreamRef.current = null;
          if (video.srcObject === stream) video.srcObject = null;
          return false;
        }
      }
      setEnrollCamOn(true);
      return true;
    } catch (e) {
      if (stream) stream.getTracks().forEach((track) => track.stop());
      if (enrollStreamRef.current === stream) enrollStreamRef.current = null;
      if (enrollVideoRef.current?.srcObject === stream) {
        enrollVideoRef.current.srcObject = null;
      }
      if (generation === enrollGenerationRef.current) {
        popToast("err", "Camera", e.message || "Permission denied");
      }
      return false;
    }
  }, [popToast]);

  const cancelRemoteSession = async (sid) => {
    if (!sid) return;
    if (mode === "device") return api.piEnrollCancel(sid);
    if (!cleanApi) throw new Error("Face API is not configured.");
    const response = await fetch(`${cleanApi}/api/enroll/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        typeof result.detail === "string" ? result.detail : response.statusText,
      );
    }
    return result;
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
    if (mode === "device") return api.piEnrollSample(sessionId, blob);
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("image", blob, "sample.jpg");
    const r = await fetch(`${cleanApi}/api/enroll/sample`, { method: "POST", body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
    return data;
  };

  const finishEnrollWithId = async (sessionId) => {
    if (mode === "device") {
      const data = await api.piEnrollFinish(sessionId);
      if (!data.ok || data.state !== "completed") throw new Error(data.message || "Backend host could not save face enrollment");
      return data;
    }
    const r = await fetch(`${cleanApi}/api/enroll/finish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    return data;
  };

  const resetEnrollUi = () => {
    clearPiEnrollPoll();
    activeEnrollRef.current = null;
    pendingEnrollCancelRef.current = null;
    setEnrollCount(0);
    stopEnrollCamera();
    setEnrollingAuto(false);
  };

  const trackActiveEnrollment = (generation, sessionId, displayName) => {
    const activeEnrollment = {
      generation,
      sessionId,
      displayName,
      cancel: () => cancelRemoteSession(sessionId),
    };
    activeEnrollRef.current = activeEnrollment;
    return activeEnrollment;
  };

  const finishPiEnroll = async (status, displayName, generation) => {
    if (generation !== enrollGenerationRef.current) return;
    clearPiEnrollPoll();
    const state = status?.state || "error";
    const enrolledName = status?.user || displayName;
    const cameraDescription =
      status?.source === "client_camera"
        ? "this device camera"
        : "the backend camera";
    enrollGenerationRef.current += 1;
    resetEnrollUi();
    if (state === "completed") {
      if (status?.recognition_available === false) {
        popToast("info", "Enrollment simulated", `${enrolledName} was added, but this fake does not store a recognition template.`);
      } else {
        popToast("ok", "Face enrolled", `${enrolledName} enrolled from ${cameraDescription}.`);
      }
      setFaceAccessAllowed((prev) => ({ ...prev, [enrolledName]: true }));
      setName("");
      await refreshLocalFaces();
    } else if (state === "cancelled") {
      popToast("info", "Cancelled", `${cameraDescription} enrollment stopped.`);
    } else {
      popToast("err", "Enrollment failed", status?.message || "Backend camera enrollment did not complete.");
    }
  };

  const settleEnrollmentCancellation = async (
    activeEnrollment,
    generation,
    failureMessage = "",
  ) => {
    if (generation !== enrollGenerationRef.current) return;
    stopEnrollCamera();
    activeEnrollRef.current = {
      ...activeEnrollment,
      generation,
      cancelling: true,
    };
    try {
      const result = await activeEnrollment.cancel();
      if (generation !== enrollGenerationRef.current) return;
      if (result?.state === "cancelled") {
        enrollGenerationRef.current += 1;
        resetEnrollUi();
        if (failureMessage) {
          popToast(
            "err",
            "Enrollment failed",
            `${failureMessage} Backend enrollment was cancelled.`,
          );
        } else {
          popToast("info", "Cancelled", "Camera enrollment stopped.");
        }
        return;
      }
      if (PI_ENROLL_FINAL_STATES.has(result?.state)) {
        await finishPiEnroll(
          result,
          activeEnrollment.displayName,
          generation,
        );
        return;
      }
      throw new Error(result?.message || "Backend did not confirm cancellation.");
    } catch (error) {
      if (generation !== enrollGenerationRef.current) return;
      const message = failureMessage
        ? `${failureMessage} Backend cancellation failed: ${error.message}`
        : error.message || "Backend did not confirm cancellation.";
      activeEnrollRef.current = {
        ...activeEnrollment,
        generation,
        cancelling: false,
      };
      setEnrollingAuto(true);
      popToast(
        "err",
        failureMessage ? "Enrollment connection lost" : "Cancel failed",
        message,
      );
    }
  };

  const finishPendingEnrollmentCancellation = (startGeneration) => {
    const pendingCancellation = pendingEnrollCancelRef.current;
    if (
      pendingCancellation?.startGeneration !== startGeneration ||
      pendingCancellation.cancelGeneration !== enrollGenerationRef.current
    ) {
      return false;
    }
    pendingEnrollCancelRef.current = null;
    enrollGenerationRef.current += 1;
    resetEnrollUi();
    popToast("info", "Cancelled", "Enrollment stopped before the camera started.");
    return true;
  };

  const cancelStaleStartedEnrollment = async (
    startGeneration,
    sessionId,
    displayName,
  ) => {
    const pendingCancellation = pendingEnrollCancelRef.current;
    if (
      pendingCancellation?.startGeneration === startGeneration &&
      pendingCancellation.cancelGeneration === enrollGenerationRef.current
    ) {
      pendingEnrollCancelRef.current = null;
      const activeEnrollment = trackActiveEnrollment(
        pendingCancellation.cancelGeneration,
        sessionId,
        displayName,
      );
      await settleEnrollmentCancellation(
        activeEnrollment,
        pendingCancellation.cancelGeneration,
      );
      return;
    }
    try {
      await cancelRemoteSession(sessionId);
    } catch {
      /* Component unmounted or a newer enrollment owns the UI. */
    }
  };

  const prepareEnrollmentUser = async (displayName) => {
    const existing = users.find(
      (user) => user.name.trim().toLowerCase() === displayName.toLowerCase(),
    );
    if (existing) {
      if (existing.is_owner && !currentUser.is_owner) {
        throw new Error("Only the owner can change the owner's face enrollment.");
      }
      return existing;
    }
    if (!/^\d{6}$/.test(newUserPin)) {
      throw new Error("A new user needs a 6-digit ASCII PIN.");
    }
    const created = await addUserToDirectory(
      displayName,
      newUserPin,
      currentUser.is_owner && newUserIsAdmin,
    );
    if (!created?.id) return null;
    setNewUserPin("");
    setNewUserIsAdmin(false);
    if (!created.enrollment_grant) {
      throw new Error(
        "The user was created, but the host did not authorize enrollment. " +
          "Update and restart the host, then retry enrollment for this user.",
      );
    }
    return created;
  };

  const authorizeEnrollment = (user) =>
    user.enrollment_grant ||
    requestGrant("enrollment.start", user.id, `enroll ${user.name}`);

  const runPiCameraEnroll = async (displayName) => {
    if (mode !== "device") {
      popToast("err", "Backend host", "Connect to the backend host before starting enrollment.");
      return;
    }

    const generation = enrollGenerationRef.current + 1;
    enrollGenerationRef.current = generation;
    pendingEnrollCancelRef.current = null;
    activeEnrollRef.current = null;
    setEnrollingAuto(true);
    setEnrollCount(0);

    let enrollmentUser;
    let grantToken;
    try {
      enrollmentUser = await prepareEnrollmentUser(displayName);
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      if (!enrollmentUser) {
        resetEnrollUi();
        return;
      }
      grantToken = await authorizeEnrollment(enrollmentUser);
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      if (!grantToken) {
        resetEnrollUi();
        return;
      }
    } catch (e) {
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      popToast("err", "Add user failed", e.message);
      resetEnrollUi();
      return;
    }

    let sessionId;
    try {
      const start = await api.piEnrollStart(
        { user_id: enrollmentUser.id, source: "device_camera" },
        grantToken,
      );
      sessionId = start.session_id || start.sessionId;
      if (generation !== enrollGenerationRef.current) {
        if (sessionId) {
          await cancelStaleStartedEnrollment(
            generation,
            sessionId,
            displayName,
          );
        } else {
          finishPendingEnrollmentCancellation(generation);
        }
        return;
      }
      if (!sessionId) {
        await finishPiEnroll(
          {
            state: "error",
            message: "Backend host did not return an enrollment session.",
          },
          displayName,
          generation,
        );
        return;
      }
      const next = {
        ...start,
        state: start.state || "capturing",
        session_id: sessionId,
        samples_needed: start.samples_needed || SAMPLES_NEEDED,
      };
      const activeEnrollment = trackActiveEnrollment(
        generation,
        sessionId,
        displayName,
      );
      setEnrollCount(next.count || 0);

      if (PI_ENROLL_FINAL_STATES.has(next.state)) {
        await finishPiEnroll(next, displayName, generation);
        return;
      }

      const poll = async () => {
        if (
          generation !== enrollGenerationRef.current ||
          activeEnrollRef.current !== activeEnrollment
        ) {
          return;
        }
        try {
          const raw = await api.piEnrollStatus(sessionId);
          if (
            generation !== enrollGenerationRef.current ||
            activeEnrollRef.current !== activeEnrollment
          ) {
            return;
          }
          const status = {
            ...raw,
            state: raw.state || "capturing",
            session_id: raw.session_id || raw.sessionId || sessionId,
            samples_needed: raw.samples_needed || SAMPLES_NEEDED,
          };
          setEnrollCount(status.count || 0);
          if (PI_ENROLL_FINAL_STATES.has(status.state)) {
            await finishPiEnroll(status, displayName, generation);
          } else {
            piEnrollPollRef.current = setTimeout(poll, 1000);
          }
        } catch (error) {
          if (
            generation !== enrollGenerationRef.current ||
            activeEnrollRef.current !== activeEnrollment
          ) {
            return;
          }
          clearPiEnrollPoll();
          await settleEnrollmentCancellation(
            activeEnrollment,
            generation,
            error.message || "Could not read enrollment status.",
          );
        }
      };
      piEnrollPollRef.current = setTimeout(poll, 1000);
      return;
    } catch (e) {
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      popToast("err", "Enrollment start failed", e.message);
      resetEnrollUi();
      return;
    }

  };

  const runAddAndEnroll = async () => {
    const n = name.trim();
    if (!n) return popToast("err", "Name required", "Enter a display name.");
    if (enrollingAuto || busy) return;
    if (enrollSource === "device_camera") {
      await runPiCameraEnroll(n);
      return;
    }
    if (mode !== "device" && !cleanApi) return popToast("err", "Face API", "Set Face API URL under Control → Connection.");

    const generation = enrollGenerationRef.current + 1;
    enrollGenerationRef.current = generation;
    pendingEnrollCancelRef.current = null;
    activeEnrollRef.current = null;
    setEnrollingAuto(true);
    setEnrollCount(0);

    let enrollmentUser;
    let grantToken;
    try {
      enrollmentUser = await prepareEnrollmentUser(n);
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      if (!enrollmentUser) {
        setEnrollingAuto(false);
        return;
      }
      grantToken = await authorizeEnrollment(enrollmentUser);
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      if (!grantToken) {
        setEnrollingAuto(false);
        return;
      }
    } catch (e) {
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      popToast("err", "Add user failed", e.message);
      setEnrollingAuto(false);
      return;
    }

    let sessionId;
    try {
      let data;
      if (mode === "device") {
        data = await api.piEnrollStart(
          { user_id: enrollmentUser.id, source: "client_camera" },
          grantToken,
        );
      } else {
        const r = await fetch(`${cleanApi}/api/enroll/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: n }),
        });
        data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : r.statusText);
      }
      sessionId = data.session_id;
      if (generation !== enrollGenerationRef.current) {
        if (sessionId) {
          await cancelStaleStartedEnrollment(generation, sessionId, n);
        } else {
          finishPendingEnrollmentCancellation(generation);
        }
        return;
      }
      if (!sessionId) {
        throw new Error("Backend host did not return an enrollment session.");
      }
    } catch (e) {
      if (generation !== enrollGenerationRef.current) {
        finishPendingEnrollmentCancellation(generation);
        return;
      }
      popToast("err", "Enrollment start failed", e.message);
      setEnrollingAuto(false);
      return;
    }

    const activeEnrollment = trackActiveEnrollment(generation, sessionId, n);

    const camOk = await startEnrollCamera(generation);
    if (generation !== enrollGenerationRef.current) return;
    if (!camOk) {
      await settleEnrollmentCancellation(
        activeEnrollment,
        generation,
        "This device camera could not start.",
      );
      return;
    }

    const video = enrollVideoRef.current;
    let waited = 0;
    while (
      video &&
      video.readyState < 2 &&
      waited < 60 &&
      generation === enrollGenerationRef.current
    ) {
      await sleep(50);
      waited++;
    }
    if (generation !== enrollGenerationRef.current) return;

    let count = 0;
    let attempts = 0;
    try {
      while (
        count < SAMPLES_NEEDED &&
        attempts < MAX_AUTO_ATTEMPTS &&
        generation === enrollGenerationRef.current
      ) {
        if (attempts > 0) await sleep(AUTO_CAPTURE_MS);
        if (generation !== enrollGenerationRef.current) break;
        attempts++;
        const blob = await grabFrameBlob();
        const data = await postSample(sessionId, blob);
        if (generation !== enrollGenerationRef.current) return;
        count = data.count ?? count;
        setEnrollCount(count);
      }
    } catch (e) {
      if (generation !== enrollGenerationRef.current) return;
      await settleEnrollmentCancellation(
        activeEnrollment,
        generation,
        `Capture failed: ${e.message}`,
      );
      return;
    }

    if (generation !== enrollGenerationRef.current) return;

    if (count < SAMPLES_NEEDED) {
      await settleEnrollmentCancellation(
        activeEnrollment,
        generation,
        `Got ${count}/${SAMPLES_NEEDED} valid samples. Keep one face in frame and try again.`,
      );
      return;
    }

    try {
      const result = await finishEnrollWithId(sessionId);
      if (generation !== enrollGenerationRef.current) return;
      const enrolledName = result.user || n;
      enrollGenerationRef.current += 1;
      resetEnrollUi();
      popToast(
        "ok",
        "Face enrolled",
        `${enrolledName} — ${result.count || result.samples || SAMPLES_NEEDED} samples saved to the backend host.`,
      );
      setFaceAccessAllowed((prev) => ({ ...prev, [enrolledName]: true }));
      setName("");
      await refreshLocalFaces();
    } catch (e) {
      if (generation !== enrollGenerationRef.current) return;
      await settleEnrollmentCancellation(
        activeEnrollment,
        generation,
        `Finish failed: ${e.message}`,
      );
    }
  };

  const onCancelEnroll = async () => {
    if (
      activeEnrollRef.current?.cancelling ||
      pendingEnrollCancelRef.current?.cancelGeneration ===
        enrollGenerationRef.current
    ) {
      return;
    }
    const startGeneration = enrollGenerationRef.current;
    const cancelGeneration = startGeneration + 1;
    const activeEnrollment = activeEnrollRef.current;
    enrollGenerationRef.current = cancelGeneration;
    clearPiEnrollPoll();
    stopEnrollCamera();

    if (!activeEnrollment?.sessionId) {
      pendingEnrollCancelRef.current = {
        startGeneration,
        cancelGeneration,
      };
      popToast(
        "info",
        "Cancellation requested",
        "Enrollment will be cancelled when the backend returns its session.",
      );
      return;
    }

    const cancellingEnrollment = {
      ...activeEnrollment,
      generation: cancelGeneration,
      cancelling: true,
    };
    activeEnrollRef.current = cancellingEnrollment;
    await settleEnrollmentCancellation(
      cancellingEnrollment,
      cancelGeneration,
    );
  };

  const updateAccess = async (user, allowed) => {
    if (user.is_owner && !currentUser.is_owner) {
      popToast("err", "Owner protected", "Only the owner can change owner access.");
      return;
    }
    try {
      const grant = await requestGrant(
        "user.access",
        user.id,
        `change access for ${user.name}`,
      );
      if (!grant) return;
      await api.setAccess(user.id, allowed, grant);
      setDeviceUsers((previous) =>
        previous.map((row) =>
          row.id === user.id ? { ...row, faceAccess: allowed } : row,
        ),
      );
    } catch (error) {
      popToast("err", "Access update failed", error.message);
    }
  };

  const resetPin = async (user) => {
    if (user.is_owner && !currentUser.is_owner) {
      popToast("err", "Owner protected", "Only the owner can change the owner PIN.");
      return;
    }
    const pin = pinDrafts[user.id] || "";
    if (!/^\d{6}$/.test(pin)) {
      popToast("err", "PIN required", "Enter a 6-digit ASCII PIN.");
      return;
    }
    try {
      const grant = await requestGrant(
        "user.pin",
        user.id,
        `reset PIN for ${user.name}`,
        { name: user.name, pin },
      );
      if (!grant) return;
      await api.resetUserPin(user.id, pin, grant);
      setPinDrafts((previous) => ({ ...previous, [user.id]: "" }));
      if (invite?.userId === user.id) setInvite(null);
      if (user.id === currentUser.id) {
        expireSession("login");
        return;
      }
      setDevices((previous) =>
        previous.map((device) =>
          device.user_id === user.id ? { ...device, active: false } : device,
        ),
      );
      popToast(
        "ok",
        "PIN updated",
        `${user.name}'s paired phones were revoked. Pair them again with the new PIN.`,
      );
      void api.devices().then(setDevices).catch(() => {});
    } catch (error) {
      popToast("err", "PIN reset failed", error.message);
    }
  };

  const createInvite = async (user) => {
    if (invitePendingRef.current) return;
    if (user.is_owner && !currentUser.is_owner) {
      popToast("err", "Owner protected", "Only the owner can pair another owner phone.");
      return;
    }
    invitePendingRef.current = true;
    setInviteBusy(true);
    try {
      const grant = await requestGrant(
        "pairing.invite",
        user.id,
        `create pairing invite for ${user.name}`,
      );
      if (!grant) return;
      setInvite(null);
      const requestedAt = Date.now();
      const result = await api.pairingInvite(user.id, grant);
      if (
        typeof result.qr_image !== "string" ||
        !result.qr_image.startsWith("data:image/png;base64,") ||
        !Number.isFinite(result.expires_in) ||
        result.expires_in <= 0
      ) {
        throw new Error("The host did not return an invitation QR. Try again.");
      }
      setInvite({
        userId: user.id,
        userName: user.name,
        qrImage: result.qr_image,
        expiresAt: requestedAt + result.expires_in * 1000,
      });
    } catch (error) {
      popToast("err", "Invite failed", error.message);
    } finally {
      invitePendingRef.current = false;
      setInviteBusy(false);
    }
  };

  const revokeDevice = async (device) => {
    const belongsToOwner = device.is_owner || device.user_is_owner;
    if (belongsToOwner && !currentUser.is_owner) {
      popToast("err", "Owner protected", "Only the owner can revoke an owner phone.");
      return;
    }
    if (!confirm(`Revoke ${device.name || "this device"}?`)) return;
    try {
      const grant = await requestGrant(
        "device.revoke",
        device.id,
        `revoke ${device.name || "device"}`,
      );
      if (!grant) return;
      await api.revokeDevice(device.id, grant);
      setDevices(await api.devices());
      popToast("ok", "Device revoked", device.name || device.id);
    } catch (error) {
      popToast("err", "Revoke failed", error.message);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-3">
      {currentUser.is_admin ? <Card>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="text-sm font-semibold text-slate-100">Enroll face</div>
          <Badge>{localFaceNames.length} enrolled</Badge>
        </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            aria-pressed={enrollSource === "device_camera"}
            className={sourceButtonClass(enrollSource === "device_camera")}
            disabled={enrollingAuto}
            onClick={() => setEnrollSource("device_camera")}
          >
            <div className="font-medium">Backend camera</div>
          </button>
          <button
            type="button"
            aria-pressed={enrollSource === "phone_camera"}
            className={sourceButtonClass(enrollSource === "phone_camera")}
            disabled={enrollingAuto}
            onClick={() => setEnrollSource("phone_camera")}
          >
            <div className="font-medium">This device camera</div>
          </button>
        </div>

        {enrollSource === "phone_camera" && mode !== "device" && !cleanApi ? (
          <div className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200/90">
            Set <span className="font-medium">Face API</span> under Control → Connection to use this device camera.
          </div>
        ) : null}

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="min-w-0 flex-1">
            <label htmlFor="display-name" className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">Display name</label>
            <Input id="display-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name" maxLength={100} disabled={enrollingAuto} />
          </div>
          <div>
            <label htmlFor="new-user-pin" className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">New user PIN</label>
            <Input
              id="new-user-pin"
              type="password"
              autoComplete="new-password"
              maxLength={6}
              inputMode="numeric"
              pattern="[0-9]{6}"
              value={newUserPin}
              onChange={(event) => setNewUserPin(event.target.value)}
              placeholder="Required for a new name"
              disabled={enrollingAuto}
            />
          </div>
          {currentUser.is_owner ? (
            <div className="flex min-h-12 items-center justify-between rounded-xl border border-white/[0.08] bg-dna-bg px-3 sm:col-span-2">
              <div>
                <div className="text-sm text-slate-200">Administrator role</div>
                <div className="text-xs text-slate-500">
                  Can manage other users; owner controls remain protected.
                </div>
              </div>
              <Switch
                checked={newUserIsAdmin}
                onChange={setNewUserIsAdmin}
                disabled={enrollingAuto}
                ariaLabel="Create this user as an administrator"
              />
            </div>
          ) : null}
          <Btn
            disabled={busy || enrollingAuto || (enrollSource === "phone_camera" && mode !== "device" && !cleanApi)}
            onClick={runAddAndEnroll}
            className="shrink-0 sm:col-span-2"
          >
            Add & enroll face
          </Btn>
        </div>

        {enrollingAuto ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge>Samples {enrollCount} / {SAMPLES_NEEDED}</Badge>
            <span className="text-xs text-slate-400">
              Keep one face in frame.
            </span>
            <Btn variant="secondary" type="button" onClick={onCancelEnroll}>
              Cancel
            </Btn>
          </div>
        ) : null}

        {enrollSource === "phone_camera" ? (
          <div className="mt-4 overflow-hidden rounded-xl border border-white/10 bg-black/40">
            <video ref={enrollVideoRef} className="aspect-video w-full object-cover" playsInline muted />
            <canvas ref={enrollCanvasRef} className="hidden" aria-hidden="true" />
          </div>
        ) : null}
      </Card> : null}

      <Card>
        <div className="text-sm font-semibold text-slate-100">
          People &amp; Access
        </div>

        <div className="mt-4 space-y-2">
          {mode !== "device" && !cleanApi ? (
            <div className="text-sm text-slate-500">Set Face API under Control → Connection to sync the roster with the face DB.</div>
          ) : accessUserNames.length === 0 ? (
            <div className="text-sm text-slate-500">
              No people yet. Add and enroll a face above.
            </div>
          ) : (
            accessUserNames.map((n) => {
              const u = users.find((x) => x.name === n);
              const ownerProtected = u?.is_owner && !currentUser.is_owner;
              const removalBlocked = !!u?.is_owner;
              const hasFace = localFaceNames.includes(n);
              const extraMeta =
                !hasFace
                  ? "Face not enrolled"
                  : !u
                    ? "Missing person record"
                    : null;
              return (
                <div
                  key={n}
                  className="flex flex-col gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-slate-100">{n}</div>
                    {u?.is_owner ? <Badge variant="info">Owner</Badge> : null}
                    {extraMeta ? (
                      <div className="text-xs text-slate-500">
                        {extraMeta}
                      </div>
                    ) : null}
                  </div>
                  {currentUser.is_admin ? <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-3">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] uppercase tracking-wide text-slate-500">
                        {isFaceAccessAllowed(n, faceAccessAllowed) ? "allowed" : "blocked"}
                      </span>
                      <Switch
                        ariaLabel={`${n} face access`}
                        checked={isFaceAccessAllowed(n, faceAccessAllowed)}
                        disabled={ownerProtected}
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
                            if (row) await updateAccess(row, allowed);
                          }
                        }}
                      />
                    </div>
                    <Btn variant="danger" disabled={busy || enrollingAuto || removalBlocked} onClick={() => removePersonByName(n)}>
                      Remove
                    </Btn>
                  </div> : null}
                </div>
              );
            })
          )}
        </div>
      </Card>

      {currentUser.is_admin ? (
        <Card>
          <div className="text-sm font-semibold text-slate-100">
            User PINs and mobile access
          </div>
          <div className="mt-4 space-y-3">
            {users.map((user) => (
              <div
                key={user.id}
                className="rounded-xl border border-white/[0.06] bg-dna-bg p-3"
              >
                <div className="text-sm font-medium text-slate-100">
                  {user.name}
                  {user.is_owner ? (
                    <span className="ml-2 text-xs font-normal text-sky-300">Owner</span>
                  ) : user.is_admin ? (
                    <span className="ml-2 text-xs font-normal text-violet-300">Administrator</span>
                  ) : null}
                </div>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <Input
                    type="password"
                    autoComplete="new-password"
                    maxLength={6}
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    value={pinDrafts[user.id] || ""}
                    onChange={(event) =>
                      setPinDrafts((previous) => ({
                        ...previous,
                        [user.id]: event.target.value,
                      }))
                    }
                    placeholder="New 6-digit PIN"
                    aria-label={`New PIN for ${user.name}`}
                    disabled={user.is_owner && !currentUser.is_owner}
                  />
                  <Btn variant="secondary" disabled={user.is_owner && !currentUser.is_owner} onClick={() => resetPin(user)}>
                    Reset PIN
                  </Btn>
                  <Btn variant="secondary" disabled={inviteBusy || (user.is_owner && !currentUser.is_owner)} onClick={() => createInvite(user)}>
                    Pair phone
                  </Btn>
                </div>
              </div>
            ))}
          </div>

          {invite ? (
            <PairingInviteCard
              key={invite.expiresAt}
              invite={invite}
              onDismiss={() => setInvite(null)}
            />
          ) : null}

          <div className="mt-6 text-sm font-semibold text-slate-100">
            Paired devices
          </div>
          <div className="mt-3 space-y-2">
            {(devices || []).length === 0 ? (
              <div className="text-sm text-slate-500">No paired phones.</div>
            ) : (
              devices.map((device) => (
                <div
                  key={device.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm text-slate-100">
                      {device.name || "Mobile device"}
                    </div>
                  <div className="text-xs text-slate-500">
                    {device.user_name} · {device.active ? "Active" : "Revoked"}
                    {device.is_owner || device.user_is_owner ? " · Owner" : ""}
                  </div>
                  </div>
                  {device.active ? (
                    <Btn variant="danger" disabled={(device.is_owner || device.user_is_owner) && !currentUser.is_owner} onClick={() => revokeDevice(device)}>
                      Revoke
                    </Btn>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
