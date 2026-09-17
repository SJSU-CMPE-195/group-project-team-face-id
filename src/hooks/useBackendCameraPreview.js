import { useCallback, useEffect, useRef, useState } from "react";

const MAX_STREAM_RETRIES = 2;

const FINAL_STATES = new Set([
  "granted",
  "denied",
  "error",
  "cancelled",
  "timeout",
  "completed",
]);

function previewErrorMessage(error) {
  const message = error?.message || "Could not reach the camera preview.";
  if (/HTTP 404\b/i.test(message)) {
    return "Camera preview needs the updated backend. Restart BASS on the host.";
  }
  return `Camera preview unavailable: ${message}`;
}

export default function useBackendCameraPreview(api, onSessionFinished) {
  const [cameraStatus, setCameraStatus] = useState(null);
  const [errorView, setErrorView] = useState(null);
  const [streamView, setStreamView] = useState(null);
  const onSessionFinishedRef = useRef(onSessionFinished);
  const lastFinishedSessionRef = useRef(null);

  useEffect(() => {
    onSessionFinishedRef.current = onSessionFinished;
  }, [onSessionFinished]);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let pollTimer = null;

    lastFinishedSessionRef.current = null;

    const requestWithTimeout = async (request, timeoutMs = 2000) => {
      const requestController = new AbortController();
      const abortRequest = () => requestController.abort();
      controller.signal.addEventListener("abort", abortRequest, { once: true });
      const timeout = setTimeout(abortRequest, timeoutMs);
      try {
        return await request(requestController.signal);
      } finally {
        clearTimeout(timeout);
        controller.signal.removeEventListener("abort", abortRequest);
      }
    };

    const poll = async () => {
      try {
        const nextStatus = await requestWithTimeout((signal) =>
          api.cameraStatus(signal),
        );
        if (disposed) return;

        const session = nextStatus?.session || null;
        const sessionId = session?.session_id || session?.sessionId || null;
        const final = Boolean(session && FINAL_STATES.has(session.state));
        const active = Boolean(sessionId && !final);
        const frameAvailable = nextStatus?.frame_available === true;

        setCameraStatus({
          api,
          cameraSource: nextStatus?.camera_source || null,
          frameAvailable,
          session,
        });
        setErrorView(null);
        setStreamView((current) => {
          if (!active || !frameAvailable) return null;
          if (current?.api === api && current.sessionId === sessionId) {
            return current;
          }
          return { api, enabled: true, failures: 0, sessionId };
        });

        if (final && sessionId) {
          const finishedKey = `${sessionId}:${session.state}`;
          if (lastFinishedSessionRef.current !== finishedKey) {
            lastFinishedSessionRef.current = finishedKey;
            void Promise.resolve(
              onSessionFinishedRef.current?.({ silent: true }),
            ).catch(() => null);
          }
        }
      } catch (error) {
        if (disposed || controller.signal.aborted) return;
        setCameraStatus(null);
        setStreamView(null);
        setErrorView({ api, message: previewErrorMessage(error) });
      } finally {
        if (!disposed) {
          pollTimer = setTimeout(
            poll,
            document.visibilityState === "visible" ? 250 : 1500,
          );
        }
      }
    };

    void poll();

    return () => {
      disposed = true;
      controller.abort();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [api]);

  useEffect(() => {
    if (
      streamView?.api !== api ||
      streamView.enabled ||
      streamView.failures > MAX_STREAM_RETRIES
    ) {
      return undefined;
    }

    const { failures, sessionId } = streamView;
    const timer = setTimeout(() => {
      setStreamView((current) => {
        if (
          current?.api !== api ||
          current.sessionId !== sessionId ||
          current.failures !== failures
        ) {
          return current;
        }
        return { ...current, enabled: true };
      });
    }, 600);
    return () => clearTimeout(timer);
  }, [api, streamView]);

  const scopedStatus = cameraStatus?.api === api ? cameraStatus : null;
  const session = scopedStatus?.session || null;
  const sessionId = session?.session_id || session?.sessionId || null;
  const final = Boolean(session && FINAL_STATES.has(session.state));
  const active = Boolean(sessionId && !final);
  const scopedStream =
    streamView?.api === api && streamView.sessionId === sessionId
      ? streamView
      : null;
  const streamAttempt = scopedStream?.failures ?? null;
  const streamKey = scopedStream
    ? `${scopedStream.sessionId}:${streamAttempt}`
    : null;
  const handleStreamError = useCallback(() => {
    setStreamView((current) => {
      if (
        current?.api !== api ||
        current.sessionId !== sessionId ||
        current.failures !== streamAttempt ||
        !current.enabled
      ) {
        return current;
      }
      return {
        ...current,
        enabled: false,
        failures: current.failures + 1,
      };
    });
  }, [api, sessionId, streamAttempt]);
  const streamUrl =
    active && scopedStatus?.frameAvailable && scopedStream?.enabled
      ? api.cameraStreamUrl(sessionId)
      : null;
  const streamError =
    active &&
    scopedStream &&
    !scopedStream.enabled &&
    scopedStream.failures > MAX_STREAM_RETRIES
      ? "Live camera stream stopped. It will retry with the next camera session."
      : null;

  return {
    active,
    cameraSource: scopedStatus?.cameraSource || null,
    onStreamError: handleStreamError,
    previewError:
      (errorView?.api === api ? errorView.message : null) || streamError,
    session,
    streamKey,
    streamUrl,
  };
}
