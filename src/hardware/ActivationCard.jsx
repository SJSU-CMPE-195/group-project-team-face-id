import React from "react";
import { QrCode } from "lucide-react";
import Btn from "../components/Btn.jsx";
import Card from "../components/Card.jsx";

export default function ActivationCard({ card, busy, disabled, onReveal }) {
  return (
    <Card>
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
        <QrCode className="h-4 w-4 text-emerald-300" />
        Activation cards
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">
        Public device identity and the one-time activation credential are
        separate. Reveal them only when you are ready to scan with the phone.
      </p>
      <Btn
        className="mt-4 w-full"
        variant="secondary"
        disabled={busy || disabled}
        onClick={onReveal}
      >
        {busy ? "Loading…" : card ? "Refresh cards" : "Reveal activation cards"}
      </Btn>
      {card ? <ActivationCodes card={card} /> : null}
    </Card>
  );
}

function ActivationCodes({ card }) {
  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-2">
      <QrCard
        title="Public device QR"
        image={card.public_qr_image}
        payload={card.public_payload}
      />
      {card.activation_payload && card.activation_qr_image ? (
        <QrCard
          title="One-time activation"
          image={card.activation_qr_image}
          payload={card.activation_payload}
          secret
        />
      ) : (
        <div className="rounded-xl border border-white/[0.06] bg-dna-bg p-3 text-xs text-slate-400">
          The one-time activation credential is no longer available after the
          device is claimed.
        </div>
      )}
    </div>
  );
}

function QrCard({ title, image, payload, secret = false }) {
  return (
    <div className="min-w-0 rounded-xl border border-white/[0.06] bg-dna-bg p-3">
      <div className="text-xs font-semibold text-slate-200">{title}</div>
      {image ? (
        <img
          className="mx-auto mt-3 aspect-square w-full max-w-52 rounded-lg bg-white p-2"
          src={image}
          alt={`${title} code`}
        />
      ) : null}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-slate-400">
          {secret ? "Show activation material" : "Show public payload"}
        </summary>
        <code className="mt-2 block max-h-28 overflow-auto break-all rounded-lg bg-black/25 p-2 text-[10px] text-slate-300">
          {typeof payload === "string" ? payload : JSON.stringify(payload)}
        </code>
      </details>
    </div>
  );
}
