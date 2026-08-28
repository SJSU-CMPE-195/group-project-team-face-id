import React from "react";
import Card from "./Card";
import Btn from "./Btn";
import Badge from "./Badge";

export default function StatusPanel({ locked, busy, doUnlock, doLock, mode, sim, status }) {
  const battery = mode === "sim" ? sim?.battery ?? 100 : status?.battery ?? 0;
  const signal  = mode === "sim" ? sim?.signal  ?? 5   : status?.signal  ?? 0;
  const online  = status?.online ?? false;

  return (
    <>
      <Card contentClassName="p-5 sm:p-6">
        <div className="flex flex-col items-center text-center">
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Current state</div>
          <div className="mt-1 text-xl font-bold text-slate-50">{locked ? "Locked" : "Unlocked"}</div>
          <p className="mt-2 max-w-md text-sm text-slate-400">
            {locked
              ? "Use Face scan above for automatic unlock when verified. Manual Unlock sends a command to your Pi in Device mode."
              : "Use Lock to return the device to a secure starting state before your next face-unlock test."}
          </p>
          <div className="mt-5 flex w-full justify-center sm:w-auto">
            {locked ? (
              <Btn disabled={busy} onClick={() => doUnlock()} className="w-full sm:w-auto">
                Unlock
              </Btn>
            ) : (
              <Btn variant="secondary" disabled={busy} onClick={() => doLock()} className="w-full sm:w-auto">
                Lock
              </Btn>
            )}
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3">
        <Card contentClassName="p-4">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Battery</div>
              <div className="mt-1 text-lg font-bold text-slate-50">{battery}%</div>
            </div>
            <Badge>{mode === "sim" ? "Sim" : "Live"}</Badge>
          </div>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
            <div
              className={`h-1.5 rounded-full transition-all ${battery > 20 ? "bg-violet-400" : "bg-rose-400"}`}
              style={{ width: `${battery}%` }}
            />
          </div>
        </Card>

        <Card contentClassName="p-4">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Signal</div>
              <div className="mt-1 text-lg font-bold text-slate-50">{signal}/5</div>
            </div>
            <Badge variant={online ? "ok" : "err"}>{mode === "device" ? (online ? "Online" : "Offline") : "Sim"}</Badge>
          </div>
          <div className="mt-3 flex items-end gap-0.5 h-4">
            {[1, 2, 3, 4, 5].map((bar) => (
              <div
                key={bar}
                className={`flex-1 rounded-sm transition-all ${bar <= signal ? "bg-violet-400" : "bg-white/10"}`}
                style={{ height: `${40 + bar * 12}%` }}
              />
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
