import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "bass.enrollment.pending";
const STORAGE_VERSION = 2;
const STATES = new Set([
  "accepted",
  "active",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);
const FINAL_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);
const DEFINITIVE_START_ERRORS = new Set([400, 401, 403, 404, 422, 429]);
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function journalStorage() {
  return ["/pc-client", "/pc-client/"].includes(window.location.pathname)
    ? localStorage
    : sessionStorage;
}

function validAcceptance(value, requestId, sessionId) {
  return (
    value === null ||
    (value?.request_id === requestId &&
      value.session_id === sessionId &&
      value.status_url === `/api/enroll/requests/${requestId}`)
  );
}

function validTerminal(value, requestId, sessionId) {
  return (
    value === null ||
    (value?.request_id === requestId &&
      value.session_id === sessionId &&
      FINAL_STATES.has(value.state) &&
      (value.result === null || typeof value.result === "object"))
  );
}

function validJournal(value) {
  return (
    value?.version === STORAGE_VERSION &&
    UUID_V4.test(value.requestId) &&
    typeof value.hostId === "string" &&
    !!value.hostId &&
    typeof value.ownerUserId === "string" &&
    !!value.ownerUserId &&
    typeof value.ownerDeviceId === "string" &&
    !!value.ownerDeviceId &&
    typeof value.userId === "string" &&
    !!value.userId &&
    typeof value.userName === "string" &&
    value.source === "device_camera" &&
    (value.sessionId === "" || typeof value.sessionId === "string") &&
    validAcceptance(value.acceptance, value.requestId, value.sessionId) &&
    validTerminal(value.terminalReceipt, value.requestId, value.sessionId)
  );
}

function storedFields(value) {
  return {
    version: STORAGE_VERSION,
    requestId: value.requestId,
    hostId: value.hostId,
    ownerUserId: value.ownerUserId,
    ownerDeviceId: value.ownerDeviceId,
    userId: value.userId,
    userName: value.userName,
    source: "device_camera",
    sessionId: value.sessionId || "",
    acceptance: value.acceptance || null,
    terminalReceipt: value.terminalReceipt || null,
  };
}

function readJournals() {
  try {
    const raw = journalStorage().getItem(STORAGE_KEY);
    if (!raw) return [];
    const stored = JSON.parse(raw);
    if (
      stored?.version !== STORAGE_VERSION ||
      !Array.isArray(stored.records) ||
      !stored.records.every(validJournal)
    ) {
      throw new Error("The saved enrollment journal is invalid.");
    }
    return stored.records;
  } catch (error) {
    throw new Error(`Enrollment recovery state is unavailable. ${error.message}`);
  }
}

function writeJournals(records) {
  const raw = JSON.stringify({ version: STORAGE_VERSION, records });
  const storage = journalStorage();
  storage.setItem(STORAGE_KEY, raw);
  if (storage.getItem(STORAGE_KEY) !== raw) {
    throw new Error("The saved enrollment journal did not match.");
  }
}

function sameScope(left, right) {
  return (
    left.hostId === right.hostId &&
    left.ownerDeviceId === right.ownerDeviceId &&
    left.ownerUserId === right.ownerUserId
  );
}

function findJournal(identity) {
  const matches = readJournals().filter((record) =>
    sameScope(record, identity),
  );
  if (matches.length > 1) {
    throw new Error("More than one enrollment is pending for this dashboard.");
  }
  return matches[0] || null;
}

function saveJournal(value) {
  try {
    const record = storedFields(value);
    const records = readJournals();
    const index = records.findIndex(
      (candidate) =>
        sameScope(candidate, record) &&
        candidate.requestId === record.requestId,
    );
    if (
      records.some(
        (candidate) =>
          sameScope(candidate, record) &&
          candidate.requestId !== record.requestId,
      )
    ) {
      throw new Error("Another enrollment is pending for this dashboard.");
    }
    if (
      index >= 0 &&
      (records[index].userId !== record.userId ||
        records[index].source !== record.source)
    ) {
      throw new Error("The saved request input changed.");
    }
    if (index >= 0) records[index] = record;
    else records.push(record);
    writeJournals(records);
  } catch (error) {
    throw new Error(`Enrollment recovery could not be saved. ${error.message}`);
  }
}

function removeJournal(value) {
  try {
    const records = readJournals();
    const remaining = records.filter(
      (candidate) =>
        !(
          sameScope(candidate, value) &&
          candidate.requestId === value.requestId
        ),
    );
    writeJournals(remaining);
  } catch (error) {
    throw new Error(
      `The result is known, but browser recovery cleanup failed. ${error.message}`,
    );
  }
}

function terminalSnapshot(receipt, active) {
  return {
    request_id: active.requestId,
    session_id: active.sessionId,
    state: receipt.state,
    result: receipt.result ?? null,
  };
}

function view(receipt, active, overrides = {}) {
  const projection = receipt?.result ?? receipt?.session ?? {};
  return {
    state: receipt?.state || projection.state || "checking",
    userName: projection.user || active.userName,
    count: projection.count ?? projection.samples ?? 0,
    samplesNeeded:
      projection.samples_needed ?? projection.samplesNeeded ?? 10,
    message: projection.message || "",
    canCancel:
      !!(receipt?.session_id || active.sessionId) &&
      !FINAL_STATES.has(receipt?.state),
    needsCheck: false,
    ...overrides,
  };
}

function recoveryView(active, message, state = "recovery") {
  return view(null, active, {
    state,
    message,
    canCancel: false,
    needsCheck: true,
  });
}

function genericRecovery(message, needsCheck = true) {
  return {
    state: "recovery",
    userName: "saved enrollment",
    count: 0,
    samplesNeeded: 10,
    message,
    canCancel: false,
    needsCheck,
  };
}

function assertReceipt(receipt, active) {
  if (
    receipt?.request_id !== active.requestId ||
    typeof receipt.session_id !== "string" ||
    !receipt.session_id ||
    !STATES.has(receipt.state) ||
    (active.sessionId && active.sessionId !== receipt.session_id)
  ) {
    throw new Error("The host returned an invalid enrollment receipt.");
  }
}

function assertAcceptance(response, active) {
  if (
    response?.request_id !== active.requestId ||
    typeof response.session_id !== "string" ||
    !response.session_id ||
    response.status_url !== `/api/enroll/requests/${active.requestId}`
  ) {
    throw new Error("The host returned an invalid enrollment acceptance.");
  }
}

export default function useEnrollment({
  api,
  busy,
  cameraLabel,
  currentUser,
  popToast,
  requestGrant,
  setDeviceUsers,
}) {
  const [enrollment, setEnrollment] = useState(null);
  const activeRef = useRef(null);
  const timerRef = useRef(null);
  const preparingRef = useRef(false);
  const recoveringRef = useRef(false);
  const generationRef = useRef(0);
  const runRef = useRef(null);
  const recoverRef = useRef(null);

  const clearTimer = useCallback(() => {
    if (!timerRef.current) return;
    clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const isCurrent = useCallback(
    (active) =>
      activeRef.current === active &&
      generationRef.current === active.generation,
    [],
  );

  const verifyIdentity = useCallback(async () => {
    const [identity, host] = await Promise.all([api.me(), api.deviceInfo()]);
    if (
      identity?.user?.id !== currentUser.id ||
      typeof identity.device_id !== "string" ||
      !identity.device_id ||
      typeof host?.device_id !== "string" ||
      !host.device_id ||
      host.protocol_version !== 4
    ) {
      throw new Error(
        "The current dashboard identity or API v4 support could not be confirmed.",
      );
    }
    return {
      hostId: host.device_id,
      ownerUserId: identity.user.id,
      ownerDeviceId: identity.device_id,
    };
  }, [api, currentUser.id]);

  const finish = useCallback(
    (active, receipt) => {
      if (!isCurrent(active)) return;
      clearTimer();
      const result = view(receipt, active);
      try {
        removeJournal(active);
      } catch (error) {
        setEnrollment(
          view(receipt, active, {
            message: error.message,
            canCancel: false,
            needsCheck: true,
          }),
        );
        return;
      }
      activeRef.current = null;
      setEnrollment(null);
      if (receipt.state === "completed") {
        setDeviceUsers((previous) =>
          previous.map((user) =>
            user.id === active.userId
              ? { ...user, faceEnrolled: true }
              : user,
          ),
        );
        popToast(
          "ok",
          "Face enrolled",
          `${result.userName} was enrolled with ${cameraLabel}.`,
        );
      } else if (receipt.state === "cancelled") {
        popToast("info", "Cancelled", `${cameraLabel} enrollment stopped.`);
      } else {
        popToast(
          "err",
          receipt.state === "interrupted"
            ? "Enrollment interrupted"
            : "Enrollment failed",
          `${result.message || "The host did not complete face enrollment."} Check that ${cameraLabel.toLowerCase()} is connected and not in use, then retry.`,
        );
      }
    },
    [cameraLabel, clearTimer, isCurrent, popToast, setDeviceUsers],
  );

  const run = useCallback(
    async (active, action = "status") => {
      if (!isCurrent(active) || active.requestPending) return;
      clearTimer();
      active.requestPending = true;
      setEnrollment((current) => ({
        ...(current || view(null, active)),
        state: active.cancelRequested
          ? "cancel_pending"
          : current?.state || "checking",
        canCancel:
          action !== "cancel" &&
          !active.cancelRequested &&
          !!active.sessionId,
        needsCheck: false,
      }));

      let nextAction = "";
      try {
        if (action === "cancel") {
          const result = await api.piEnrollCancel(active.sessionId);
          if (FINAL_STATES.has(result?.state)) {
            active.terminalReceipt = terminalSnapshot(
              { state: result.state, result },
              active,
            );
            saveJournal(active);
          }
          if (!isCurrent(active)) return;
          if (active.terminalReceipt) finish(active, active.terminalReceipt);
          else nextAction = "status";
        } else {
          const receipt = await api.piEnrollRequest(active.requestId);
          assertReceipt(receipt, active);
          const learnedSession = !active.sessionId;
          if (learnedSession) active.sessionId = receipt.session_id;
          if (FINAL_STATES.has(receipt.state)) {
            active.terminalReceipt = terminalSnapshot(receipt, active);
          }
          if (learnedSession || active.terminalReceipt) saveJournal(active);
          if (!isCurrent(active)) return;
          if (active.terminalReceipt) {
            finish(active, active.terminalReceipt);
          } else if (active.cancelRequested) {
            nextAction = "cancel";
          } else {
            setEnrollment(view(receipt, active));
            timerRef.current = setTimeout(
              () => void runRef.current?.(active),
              1000,
            );
          }
        }
      } catch (error) {
        if (!isCurrent(active)) return;
        setEnrollment(
          recoveryView(
            active,
            `${action === "cancel" ? "Cancellation" : "The enrollment result"} is not confirmed. ${error.message}`,
            active.cancelRequested ? "cancel_pending" : "recovery",
          ),
        );
      } finally {
        active.requestPending = false;
      }
      if (nextAction && isCurrent(active)) {
        await runRef.current?.(active, nextAction);
      }
    },
    [api, clearTimer, finish, isCurrent],
  );

  const recoverCurrent = useCallback(async () => {
    if (recoveringRef.current || activeRef.current) return;
    const generation = generationRef.current;
    recoveringRef.current = true;
    let records;
    try {
      records = readJournals();
      if (!records.length) {
        if (generationRef.current === generation) setEnrollment(null);
        return;
      }
      if (generationRef.current === generation) {
        setEnrollment(
          genericRecovery("Checking saved enrollment requests…", false),
        );
      }
      const identity = await verifyIdentity();
      if (generationRef.current !== generation) return;
      const pending = findJournal(identity);
      if (!pending) {
        setEnrollment(null);
        return;
      }
      const active = {
        ...pending,
        generation,
        cancelRequested: false,
        requestPending: false,
        startPending: false,
      };
      activeRef.current = active;
      if (active.terminalReceipt) finish(active, active.terminalReceipt);
      else await runRef.current?.(active);
    } catch (error) {
      if (generationRef.current === generation) {
        setEnrollment(
          genericRecovery(
            `Saved enrollment recovery could not be checked. ${error.message}`,
          ),
        );
      }
    } finally {
      if (generationRef.current === generation) recoveringRef.current = false;
    }
  }, [finish, verifyIdentity]);

  const checkEnrollment = useCallback(async () => {
    const active = activeRef.current;
    if (!active) {
      await recoverRef.current?.();
      return;
    }
    if (active.startPending || active.requestPending) return;
    await runRef.current?.(active);
  }, []);

  useEffect(() => {
    runRef.current = run;
    recoverRef.current = recoverCurrent;
  }, [recoverCurrent, run]);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    clearTimer();
    activeRef.current = null;
    preparingRef.current = false;
    recoveringRef.current = false;
    setEnrollment(null);
    void recoverRef.current?.();

    return () => {
      if (generationRef.current === generation) {
        generationRef.current += 1;
      }
      clearTimer();
      activeRef.current = null;
      preparingRef.current = false;
      recoveringRef.current = false;
    };
  }, [
    api,
    clearTimer,
    currentUser.id,
    currentUser.is_admin,
    currentUser.is_owner,
  ]);

  const startEnrollment = useCallback(
    async (user) => {
      if (
        activeRef.current ||
        preparingRef.current ||
        recoveringRef.current ||
        busy
      ) {
        return;
      }
      if (user.is_owner && !currentUser.is_owner) {
        popToast(
          "err",
          "Owner only",
          "Only the owner can change this face enrollment.",
        );
        return;
      }
      const generation = generationRef.current;
      preparingRef.current = true;
      setEnrollment(
        view(null, { userName: user.name }, { state: "authorizing" }),
      );
      try {
        const identity = await verifyIdentity();
        if (generationRef.current !== generation) return;
        const grant = await requestGrant(
          "enrollment.start",
          user.id,
          `enroll ${user.name} with ${cameraLabel}`,
        );
        if (generationRef.current !== generation) return;
        if (!grant) {
          setEnrollment(null);
          return;
        }
        const confirmedIdentity = await verifyIdentity();
        if (generationRef.current !== generation) return;
        if (!sameScope(identity, confirmedIdentity)) {
          throw new Error("The dashboard identity changed during authorization.");
        }
        const active = {
          version: STORAGE_VERSION,
          requestId: crypto.randomUUID().toLowerCase(),
          ...identity,
          userId: user.id,
          userName: user.name,
          source: "device_camera",
          sessionId: "",
          acceptance: null,
          terminalReceipt: null,
          generation,
          cancelRequested: false,
          requestPending: true,
          startPending: true,
        };
        saveJournal(active);
        if (generationRef.current !== generation) return;
        activeRef.current = active;
        preparingRef.current = false;
        setEnrollment(
          view(null, active, {
            state: "starting",
            message: "The request ID is saved. Waiting for host acceptance…",
            canCancel: true,
          }),
        );
        try {
          const accepted = await api.piEnrollStart(
            active.requestId,
            active.userId,
            grant,
          );
          assertAcceptance(accepted, active);
          active.sessionId = accepted.session_id;
          active.acceptance = accepted;
          active.requestPending = false;
          active.startPending = false;
          saveJournal(active);
          if (!isCurrent(active)) return;
          await runRef.current?.(
            active,
            active.cancelRequested ? "cancel" : "status",
          );
        } catch (error) {
          active.requestPending = false;
          active.startPending = false;
          if (DEFINITIVE_START_ERRORS.has(error.status)) {
            let cleanupError = null;
            try {
              removeJournal(active);
            } catch (storageError) {
              cleanupError = storageError;
            }
            if (!isCurrent(active)) return;
            if (cleanupError) {
              setEnrollment(recoveryView(active, cleanupError.message));
            } else {
              activeRef.current = null;
              setEnrollment(null);
              popToast("err", "Enrollment not started", error.message);
            }
            return;
          }
          if (!isCurrent(active)) return;
          setEnrollment(
            recoveryView(
              active,
              `Host acceptance is uncertain. ${error.message}`,
            ),
          );
          await runRef.current?.(active);
        }
      } catch (error) {
        if (generationRef.current === generation && !activeRef.current) {
          setEnrollment(null);
          popToast("err", "Enrollment not started", error.message);
        }
      } finally {
        if (generationRef.current === generation) {
          preparingRef.current = false;
        }
      }
    },
    [
      api,
      busy,
      cameraLabel,
      currentUser.is_owner,
      isCurrent,
      popToast,
      requestGrant,
      verifyIdentity,
    ],
  );

  const cancelEnrollment = useCallback(async () => {
    const active = activeRef.current;
    if (!active || active.cancelRequested || !isCurrent(active)) return;
    active.cancelRequested = true;
    clearTimer();
    setEnrollment((current) => ({
      ...current,
      state: "cancel_pending",
      message: active.startPending
        ? "Waiting for the saved request receipt before cancelling…"
        : "Checking the saved request before cancelling…",
      canCancel: false,
      needsCheck: !active.startPending && !active.requestPending,
    }));
    if (active.startPending || active.requestPending) return;
    await runRef.current?.(active, active.sessionId ? "cancel" : "status");
  }, [clearTimer, isCurrent]);

  return {
    enrollment,
    startEnrollment,
    cancelEnrollment,
    checkEnrollment,
  };
}
