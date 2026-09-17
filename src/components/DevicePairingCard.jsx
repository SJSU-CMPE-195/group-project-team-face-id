import React, { useEffect, useRef, useState } from "react";
import Btn from "./Btn";
import Card from "./Card";

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

export default function DevicePairingCard() {
  const localBrowser = LOCAL_HOSTNAMES.has(
    window.location.hostname.toLowerCase(),
  );
  const requestRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [pairing, setPairing] = useState(null);

  useEffect(() => {
    return () => {
      const request = requestRef.current;
      requestRef.current = null;
      request?.abort();
    };
  }, []);

  async function loadPairing() {
    requestRef.current?.abort();

    const request = new AbortController();
    let timedOut = false;
    requestRef.current = request;
    setLoading(true);
    setError("");
    setPairing(null);

    const timeout = window.setTimeout(() => {
      timedOut = true;
      request.abort();
    }, 8000);

    try {
      const response = await fetch(
        `${window.location.origin}/local/pairing-qr`,
        {
          method: "GET",
          cache: "no-store",
          redirect: "error",
          signal: request.signal,
        },
      );

      if (!response.ok) {
        throw new Error(`Pairing service returned ${response.status}.`);
      }

      const payload = await response.json();
      const hasIdentity =
        typeof payload?.device_id === "string" &&
        typeof payload?.name === "string";
      const hasPng =
        typeof payload?.qr_image === "string" &&
        payload.qr_image.startsWith("data:image/png;base64,");

      if (!hasIdentity || !hasPng) {
        throw new Error("Pairing service returned an invalid response.");
      }

      const isCurrentRequest = requestRef.current === request;
      if (!isCurrentRequest || request.signal.aborted) return;

      setPairing({
        name: payload.name,
        qrImage: payload.qr_image,
      });
    } catch {
      const isCurrentRequest = requestRef.current === request;
      if (!isCurrentRequest) return;

      setError(
        timedOut
          ? "The wireless host did not respond within 8 seconds."
          : "Could not load this device's pairing QR.",
      );
    } finally {
      window.clearTimeout(timeout);

      if (requestRef.current === request) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }

  function showPairing() {
    setOpen(true);
    void loadPairing();
  }

  function hidePairing() {
    const request = requestRef.current;
    requestRef.current = null;
    request?.abort();
    setOpen(false);
    setLoading(false);
    setError("");
    setPairing(null);
  }

  return (
    <Card>
      <div className="text-sm font-semibold text-slate-100">
        Device pairing
      </div>

      {!localBrowser ? (
        <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/10 p-4 text-sm leading-relaxed text-amber-200">
          Open this page on the host using localhost to view its QR.
        </div>
      ) : (
        <>
          <div className="mt-4">
            <Btn
              variant={open ? "secondary" : "primary"}
              onClick={open ? hidePairing : showPairing}
              className="w-full sm:w-auto"
            >
              {open ? "Hide QR" : "Show QR"}
            </Btn>
          </div>

          {open && loading && (
            <div
              role="status"
              className="mt-4 rounded-xl border border-white/[0.06] bg-dna-bg p-4 text-sm text-slate-400"
            >
              Loading QR…
            </div>
          )}

          {open && error && (
            <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/10 p-4">
              <div role="alert" className="text-sm text-rose-200">
                {error}
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                Start the host, then retry.
              </p>
              <Btn
                variant="secondary"
                onClick={() => void loadPairing()}
                className="mt-3 w-full sm:w-auto"
              >
                Retry
              </Btn>
            </div>
          )}

          {open && pairing && (
            <div className="mt-4 space-y-4">
              <div className="break-words text-center text-sm font-semibold text-slate-100">
                {pairing.name}
              </div>

              <div className="mx-auto w-full max-w-xs rounded-2xl bg-white p-4 shadow-lg shadow-black/25">
                <img
                  src={pairing.qrImage}
                  alt={`Pairing QR for ${pairing.name}`}
                  className="aspect-square w-full object-contain"
                  draggable="false"
                />
              </div>

              <p className="text-center text-xs leading-relaxed text-amber-300/90">
                Scanning this QR grants control of this device.
              </p>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
