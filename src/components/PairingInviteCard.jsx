import React, { useEffect, useState } from "react";
import Btn from "./Btn.jsx";

export default function PairingInviteCard({ invite, onDismiss }) {
  const [now, setNow] = useState(Date.now);
  const [imageFailed, setImageFailed] = useState(false);
  const secondsLeft = Math.max(0, Math.ceil((invite.expiresAt - now) / 1000));
  const expired = secondsLeft === 0;

  useEffect(() => {
    if (expired) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expired]);

  return (
    <div className="mt-4 rounded-xl border border-violet-400/25 bg-violet-400/10 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-violet-100">
          Pair a phone for {invite.userName}
        </h3>
        <Btn variant="secondary" onClick={onDismiss}>
          Hide QR
        </Btn>
      </div>
      {!expired && !imageFailed ? (
        <>
          <p className="mt-3 text-sm text-slate-300">
            Scan this QR in the BASS app, then enter {invite.userName}'s 6-digit
            PIN on the phone.
          </p>
          <img
            src={invite.qrImage}
            alt={`Phone pairing invitation for ${invite.userName}`}
            className="mx-auto mt-4 block w-full max-w-80 rounded-xl bg-white p-2"
            onError={() => setImageFailed(true)}
          />
          <p className="mt-3 text-center text-xs text-slate-400">
            One use · Expires in {secondsLeft} seconds
          </p>
        </>
      ) : (
        <p className="mt-3 text-sm text-amber-200" role="status">
          {imageFailed
            ? "Could not display the invitation. Choose Pair phone again."
            : "This invitation expired. Choose Pair phone again."}
        </p>
      )}
    </div>
  );
}
