import React from "react";
import Card from "./Card";
import Btn from "./Btn";
import Badge from "./Badge";

export default function StatusPanel({ locked, busy, doLock, status }) {
  const battery = status?.battery ?? 0;
  const signal = status?.signal ?? 0;
  const online  = status?.online ?? false;

  return (
    <>
      <Card contentClassName="p-5 sm:p-6">
        <div className="flex flex-col items-center text-center">
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Current state</div>
          <div className="mt-1 text-xl font-bold text-slate-50">{locked ? "Locked" : "Unlocked"}</div>
          {!locked ? (
            <div className="mt-5 flex w-full justify-center sm:w-auto">
              <Btn variant="secondary" disabled={busy} onClick={() => doLock()} className="w-full sm:w-auto">
                Lock
              </Btn>
            </div>
          ) : null}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3">
        <Card contentClassName="p-4">
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Battery</div>
          <div className="mt-1 text-lg font-bold text-slate-50">{battery}%</div>
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
            <Badge variant={online ? "ok" : "err"}>{online ? "Online" : "Offline"}</Badge>
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
