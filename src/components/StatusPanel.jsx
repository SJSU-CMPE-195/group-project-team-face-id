import React from "react";
import Card from "./Card";
import Btn from "./Btn";

export default function StatusPanel({ locked, busy, doUnlock, doLockSim }) {
  return (
    <Card contentClassName="p-5 sm:p-6">
      <div className="flex flex-col items-center text-center">
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Current state</div>
        <div className="mt-1 text-xl font-bold text-slate-50">{locked ? "Locked" : "Unlocked"}</div>
        <p className="mt-2 max-w-md text-sm text-slate-400">
          {locked
            ? "Use Face scan above for automatic unlock when verified. Manual Unlock sends a command to your Pi in Device mode."
            : "Lock (sim) is simulation-only. In Device mode, re-lock from your hardware workflow if needed."}
        </p>
        <div className="mt-5 flex justify-center">
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
  );
}
