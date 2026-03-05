import React from "react";
import Card from "./Card";
import Btn from "./Btn";
import Badge from "./Badge";

export default function StatusPanel({ locked, busy, doUnlock, doLockSim, mode, sim, status }) {
  return (
    <>
      <Card>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs text-neutral-500">Current state</div>
            <div className="mt-1 text-lg font-semibold">{locked ? "Locked" : "Unlocked"}</div>
            <div className="mt-2 text-sm text-neutral-600">Tap to unlock (demo). In Device API mode this sends a command to your Pi.</div>
          </div>
          <div className="flex items-center gap-2">
            {locked ? (
              <Btn disabled={busy} onClick={doUnlock}>
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
              <div className="text-xs text-neutral-500">Battery</div>
              <div className="mt-1 text-lg font-semibold">{mode === "sim" ? sim.battery : status.battery}%</div>
            </div>
            <Badge>Demo</Badge>
          </div>
          <div className="mt-3 h-2 w-full rounded-full bg-neutral-100">
            <div
              className="h-2 rounded-full bg-neutral-900"
              style={{ width: `${mode === "sim" ? sim.battery : status.battery}%` }}
            />
          </div>
        </Card>
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-neutral-500">Signal</div>
              <div className="mt-1 text-lg font-semibold">{mode === "sim" ? sim.signal : status.signal}/5</div>
            </div>
            <Badge>{status.online ? "Online" : "Offline"}</Badge>
          </div>
          <div className="mt-2 text-sm text-neutral-600">Use same Wi-Fi router, or Pi hotspot later.</div>
        </Card>
      </div>
    </>
  );
}
