import { useCallback, useEffect, useRef, useState } from "react";

function makePressId() {
  return crypto.randomUUID();
}

export default function useMultifunctionButton(simulator, disabled) {
  const { buttonCancel, buttonDown, buttonKeepalive, buttonUp } = simulator;
  const [held, setHeld] = useState(false);
  const [error, setError] = useState("");
  const activeRef = useRef(null);
  const mountedRef = useRef(true);

  const stopHeartbeat = useCallback((press) => {
    if (press?.heartbeat) {
      window.clearInterval(press.heartbeat);
      press.heartbeat = null;
    }
  }, []);

  const failPress = useCallback(
    (press, requestError) => {
      stopHeartbeat(press);
      if (activeRef.current === press) activeRef.current = null;
      if (!mountedRef.current) return;
      setHeld(false);
      setError(
        requestError?.message ||
          "The button connection was interrupted. The incomplete press was cancelled.",
      );
    },
    [stopHeartbeat],
  );

  const finish = useCallback(
    (action = "up") => {
      const press = activeRef.current;
      if (!press) return;
      activeRef.current = null;
      stopHeartbeat(press);
      if (mountedRef.current) setHeld(false);

      const finishRequest =
        action === "up" ? buttonUp : buttonCancel;
      press.chain = press.chain
        .then(() => finishRequest(press.id))
        .catch((requestError) => failPress(press, requestError));
    },
    [buttonCancel, buttonUp, failPress, stopHeartbeat],
  );

  const begin = useCallback(() => {
    if (disabled || activeRef.current) return;
    const press = {
      id: makePressId(),
      chain: Promise.resolve(),
      heartbeat: null,
    };
    activeRef.current = press;
    setError("");
    setHeld(true);

    const down = buttonDown(press.id).then(() => {
        if (activeRef.current !== press) return;
        const scheduleHeartbeat = () => {
          press.heartbeat = window.setTimeout(() => {
            press.heartbeat = null;
            if (activeRef.current !== press) return;
            const keepalive = press.chain.then(() => buttonKeepalive(press.id));
            press.chain = keepalive;
            keepalive.then(
              () => {
                if (activeRef.current === press) scheduleHeartbeat();
              },
              (requestError) => failPress(press, requestError),
            );
          }, 500);
        };
        scheduleHeartbeat();
      });
    press.chain = down;
    down.catch((requestError) => failPress(press, requestError));
  }, [buttonDown, buttonKeepalive, disabled, failPress]);

  useEffect(() => {
    if (!disabled) return undefined;
    const timer = window.setTimeout(() => finish("cancel"), 0);
    return () => window.clearTimeout(timer);
  }, [disabled, finish]);

  useEffect(() => {
    mountedRef.current = true;
    const cancel = () => finish("cancel");
    const handleVisibility = () => {
      if (document.visibilityState === "hidden") cancel();
    };
    window.addEventListener("blur", cancel);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("blur", cancel);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [finish]);

  useEffect(
    () => () => {
      const press = activeRef.current;
      if (!press) return;
      activeRef.current = null;
      stopHeartbeat(press);
      press.chain
        .then(() => buttonCancel(press.id))
        .catch(() => {});
    },
    [buttonCancel, stopHeartbeat],
  );

  return {
    held,
    error,
    begin,
    release: () => finish("up"),
    cancel: () => finish("cancel"),
  };
}
