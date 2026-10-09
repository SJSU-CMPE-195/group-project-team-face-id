import React, { useEffect, useMemo, useState } from "react";
import { Copy, Download } from "lucide-react";
import Btn from "./Btn.jsx";

function exportName(invite) {
  const suffix = String(invite.userId || "account")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .slice(0, 80);
  return `bass-invite-${suffix || "account"}.json`;
}

function downloadJson(contents, name) {
  const url = URL.createObjectURL(
    new Blob([contents], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function PairingInviteCard({ invite, onDismiss }) {
  const [now, setNow] = useState(Date.now);
  const [imageFailed, setImageFailed] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const secondsLeft = Math.max(0, Math.ceil((invite.expiresAt - now) / 1000));
  const expired = secondsLeft === 0;
  const invitationJson = useMemo(
    () => `${JSON.stringify(invite.qrPayload, null, 2)}\n`,
    [invite.qrPayload],
  );

  async function copyInvitation() {
    try {
      await navigator.clipboard.writeText(invitationJson);
      setCopyStatus("Invitation JSON copied.");
    } catch {
      setCopyStatus("Copy failed. Download the JSON file instead.");
    }
  }

  useEffect(() => {
    if (expired) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expired]);

  return (
    <div className="mt-4 rounded-xl border border-violet-400/25 bg-violet-400/10 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-violet-100">
          Pair a device for {invite.userName}
        </h3>
        <Btn variant="secondary" onClick={onDismiss}>
          Hide invitation
        </Btn>
      </div>
      {!expired ? (
        <>
          <p className="mt-3 text-sm text-slate-300">
            Scan this QR in BASS on Android, or download its JSON and import it in
            the BASS PC client. Then enter {invite.userName}'s 6-digit PIN on that
            device.
          </p>
          {!imageFailed ? (
            <img
              src={invite.qrImage}
              alt={`Device pairing invitation for ${invite.userName}`}
              className="mx-auto mt-4 block w-full max-w-80 rounded-xl bg-white p-2"
              onError={() => setImageFailed(true)}
            />
          ) : (
            <p className="mt-3 text-sm text-amber-200" role="status">
              The QR image could not be displayed. Use the JSON invitation below.
            </p>
          )}
          <p className="mt-3 text-center text-xs text-slate-400">
            One use · Expires in {secondsLeft} seconds
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <Btn variant="secondary" onClick={() => void copyInvitation()}>
              <Copy className="mr-2 h-4 w-4" />
              Copy JSON
            </Btn>
            <Btn
              variant="secondary"
              onClick={() => downloadJson(invitationJson, exportName(invite))}
            >
              <Download className="mr-2 h-4 w-4" />
              Download JSON
            </Btn>
          </div>
          {copyStatus ? (
            <p className="mt-2 text-center text-xs text-slate-300" role="status">
              {copyStatus}
            </p>
          ) : null}
        </>
      ) : (
        <p className="mt-3 text-sm text-amber-200" role="status">
          This invitation expired. Choose Pair device again.
        </p>
      )}
    </div>
  );
}
