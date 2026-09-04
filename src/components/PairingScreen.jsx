import React, { useState } from "react";
import { ShieldCheck, KeyRound } from "lucide-react";
import Card from "./Card";
import Btn from "./Btn";
import Input from "./Input";

/**
 * Shown when this browser has no valid session.
 *
 * The code is single-use and expires in five minutes; an administrator mints
 * it on the Pi. Redeeming it sets an HttpOnly cookie, so nothing secret is
 * ever held by this component -- once submitted, the code is of no further
 * use and the browser cannot read the session that replaced it.
 */
export default function PairingScreen({ onPair, busy }) {
  const [code, setCode] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    const ok = await onPair(code);
    if (ok) setCode("");
  };

  return (
    <div className="mx-auto flex w-full max-w-md flex-col justify-center px-4 py-10">
      <Card contentClassName="p-6 sm:p-8">
        <div className="flex flex-col items-center text-center">
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 shadow-lg shadow-violet-900/40">
            <ShieldCheck className="h-6 w-6 text-white" strokeWidth={2} />
          </div>
          <h1 className="text-lg font-semibold text-slate-100">Pair this device</h1>
          <p className="mt-2 text-sm text-slate-400">
            Ask an administrator for a pairing code. Codes work once and expire
            after five minutes.
          </p>
        </div>

        <form onSubmit={submit} className="mt-6 space-y-3">
          <label
            htmlFor="pairing-code"
            className="block text-[11px] font-medium uppercase tracking-wider text-slate-500"
          >
            Pairing code
          </label>
          <Input
            id="pairing-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Paste the code from the Pi"
            autoComplete="one-time-code"
            spellCheck={false}
            disabled={busy}
          />
          <Btn type="submit" disabled={busy || !code.trim()} className="w-full gap-2">
            <KeyRound aria-hidden="true" size={16} />
            {busy ? "Pairing…" : "Pair device"}
          </Btn>
        </form>

        <p className="mt-6 border-t border-white/[0.06] pt-4 text-center text-xs leading-relaxed text-slate-500">
          On the Pi, an administrator can generate one with{" "}
          <span className="font-mono text-[11px] text-slate-400">
            sudo faceid-pair &lt;name&gt;
          </span>
          .
        </p>
      </Card>
    </div>
  );
}
