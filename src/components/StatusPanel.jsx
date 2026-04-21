import React from "react";
import Card from "./Card";
import Btn from "./Btn";
import Badge from "./Badge";

export default function StatusPanel({ locked, busy, doLockSim, mode, sim, status }) {
  const battery = mode === "sim" ? sim.battery : status.battery;
  const signal = mode === "sim" ? sim.signal : status.signal;

  return (
    <div className="space-y-3">
      <Card>
        <div className="flex flex-col gap-4">
          <div className="text-center">
            <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Current state</div>
            <div className="mt-1 text-xl font-bold text-slate-50">{locked ? "Locked" : "Unlocked"}</div>
            <div className="mx-auto mt-2 max-w-md text-sm text-slate-400">
              {locked
                ? "Use Face scan above: when verification succeeds, the app unlocks automatically."
                : "Lock (sim) is simulation-only. In Device mode, re-lock from your hardware workflow if needed."}
            </div>
          </div>
          {!locked ? (
            <div className="flex w-full justify-center border-t border-white/[0.06] pt-5">
              <Btn
                variant="secondary"
                disabled={busy}
                onClick={doLockSim}
                className="min-w-[14rem] px-10 py-3.5 text-base font-semibold"
              >
                Lock (sim)
              </Btn>
            </div>
          ) : null}
        </div>
      </Card>

      <Card>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Device telemetry</div>
        <div className="mt-4 grid grid-cols-1 gap-6 border-t border-white/[0.06] pt-4 sm:grid-cols-2 sm:gap-8">
          <div>
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Battery</div>
                <div className="mt-1 text-2xl font-bold tabular-nums text-slate-100">{battery}%</div>
              </div>
              <Badge>Demo</Badge>
            </div>
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-dna-bg">
              <div
                className="h-full rounded-full bg-gradient-to-r from-violet-600 to-fuchsia-500 transition-[width] duration-300"
                style={{ width: `${battery}%` }}
              />
            </div>
          </div>
          <div className="sm:border-l sm:border-white/[0.06] sm:pl-8">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Signal</div>
                <div className="mt-1 text-2xl font-bold tabular-nums text-slate-100">
                  {signal}
                  <span className="text-lg font-semibold text-slate-500">/5</span>
                </div>
              </div>
              <Badge>{status.online ? "Online" : "Offline"}</Badge>
            </div>
            <p className="mt-3 text-sm leading-snug text-slate-400">Same LAN as the Pi, or Pi hotspot when available.</p>
          </div>
        </div>
      </Card>
    </div>
  );
}
