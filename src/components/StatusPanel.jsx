import React from "react";
import Card from "./Card";
import Btn from "./Btn";
import Badge from "./Badge";

export default function StatusPanel({ locked, busy, doUnlock, doLockSim, mode, sim, status }) {
  const battery = mode === "sim" ? sim.battery : status.battery;
  const signal = mode === "sim" ? sim.signal : status.signal;

  return (
    <>
      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Current state</div>
            <div className="mt-1 text-xl font-bold text-slate-50">{locked ? "Locked" : "Unlocked"}</div>
            <div className="mt-2 max-w-md text-sm text-slate-400">
              {locked
                ? "Use Face scan above for automatic unlock when verified. Manual Unlock sends a command to your Pi in Device mode."
                : "Lock (sim) is simulation-only. In Device mode, re-lock from your hardware workflow if needed."}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {locked ? (
              <Btn disabled={busy} onClick={() => doUnlock()}>
                Unlock
              </Btn>
            ) : (
              <Btn variant="secondary" disabled={busy} onClick={doLockSim}>
                Lock (sim)
              </Btn>
            )}
          </div>
        </div>
      </Card>

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Battery</div>
              <div className="mt-1 text-lg font-bold text-slate-100">{battery}%</div>
            </div>
            <Badge>Demo</Badge>
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-dna-bg">
            <div
              className="h-full rounded-full bg-gradient-to-r from-violet-600 to-fuchsia-500 transition-[width] duration-300"
              style={{ width: `${battery}%` }}
            />
          </div>
        </Card>
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Signal</div>
              <div className="mt-1 text-lg font-bold text-slate-100">{signal}/5</div>
            </div>
            <Badge>{status.online ? "Online" : "Offline"}</Badge>
          </div>
          <div className="mt-2 text-sm text-slate-400">Same LAN as the Pi, or Pi hotspot when available.</div>
        </Card>
      </div>
    </>
  );
}
