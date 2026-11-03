import React, { useCallback, useEffect, useRef, useState } from "react";
import { QrCode } from "lucide-react";
import useApi from "../hooks/useApi.js";
import useSecurity from "../security/useSecurity.js";
import Btn from "./Btn.jsx";
import Card from "./Card.jsx";

function pairingQr(response) {
  if (
    typeof response?.name !== "string" ||
    !response.name.trim() ||
    typeof response.device_id !== "string" ||
    !response.device_id ||
    typeof response.qr_image !== "string" ||
    !response.qr_image.startsWith("data:image/png;base64,")
  ) {
    throw new Error("The host returned an invalid pairing QR.");
  }
  return response;
}

export default function DevicePairingCard() {
  const api = useApi("/local/wireless");
  const { sessionEpoch, user } = useSecurity();
  const requestRef = useRef(null);
  const [qr, setQr] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  let actionLabel = "Show pairing QR";
  if (loading) actionLabel = "Loading…";
  else if (qr) actionLabel = "Hide pairing QR";
  else if (error) actionLabel = "Retry";

  const hideQr = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    setQr(null);
    setLoading(false);
    setError("");
  }, []);

  useEffect(() => {
    hideQr();
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, [hideQr, sessionEpoch, user?.id]);

  async function showQr() {
    requestRef.current?.abort();
    const request = new AbortController();
    requestRef.current = request;
    setLoading(true);
    setError("");
    try {
      const response = pairingQr(await api.pairingQr(request.signal));
      if (requestRef.current !== request) return;
      setQr(response);
    } catch (requestError) {
      if (requestError.name === "AbortError") return;
      if (requestRef.current !== request) return;
      setError(requestError.message || "The pairing QR could not be loaded.");
    } finally {
      if (requestRef.current === request) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }

  return (
    <Card>
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
        <QrCode className="h-4 w-4 text-violet-300" />
        Device pairing QR
      </div>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        This fixed QR identifies this BASS host. To authorize a new phone, create
        an invitation in Users → Pair device, then enter that account&apos;s PIN.
      </p>

      {qr ? (
        <div className="mt-4 rounded-xl border border-white/[0.06] bg-dna-bg p-4">
          <div className="text-sm font-semibold text-slate-100">{qr.name}</div>
          <div className="mt-1 break-all text-xs text-slate-500">
            Device ID: {qr.device_id}
          </div>
          <img
            className="mx-auto mt-4 aspect-square w-full max-w-80 rounded-xl bg-white p-2"
            src={qr.qr_image}
            alt={`Pairing QR for ${qr.name}`}
            onError={() => {
              setQr(null);
              setError("The pairing QR image could not be displayed.");
            }}
          />
          <p className="mt-3 text-center text-xs text-slate-400">
            This code identifies the host but does not grant access by itself.
          </p>
        </div>
      ) : null}

      {error ? (
        <p className="mt-3 text-sm text-amber-200" role="alert">
          {error}
        </p>
      ) : null}

      <Btn
        className="mt-4 w-full"
        variant="secondary"
        disabled={loading}
        onClick={qr ? hideQr : () => void showQr()}
      >
        {actionLabel}
      </Btn>
    </Card>
  );
}
