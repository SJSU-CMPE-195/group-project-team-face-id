import React from "react";
import { Shield } from "lucide-react";
import Btn from "./Btn";

export default function Header({ busy, onRefresh }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex gap-4">
        <div
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-teal-500/25 bg-teal-500/10 shadow-[0_0_24px_-8px_rgba(45,212,191,0.45)]"
          aria-hidden
        >
          <Shield className="h-6 w-6 text-teal-400" strokeWidth={1.75} />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-teal-500/90">BASS</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-50 md:text-3xl">Vehicle access console</h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-400">
            Connect to your Pi, enroll drivers, unlock, and review logs. Use Simulation for demos without hardware.
          </p>
        </div>
      </div>
      <Btn variant="secondary" disabled={busy} onClick={onRefresh}>
        {busy ? "…" : "Refresh"}
      </Btn>
    </div>
  );
}
