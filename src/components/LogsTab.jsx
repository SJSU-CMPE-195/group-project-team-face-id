import React from "react";
import Card from "./Card";
import Btn from "./Btn";
import { fmt } from "../utils/helpers";

export default function LogsTab({ mode, sim, deviceLogs, clearLogs }) {
  const logs = mode === "device" ? deviceLogs : sim.logs;
  const canClear = mode === "sim";

  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Event log</div>
          <div className="mt-1 text-xs text-neutral-600">
            {mode === "device" ? "Events from Pi (auth_logs)." : "Enroll/unlock/settings events."}
          </div>
        </div>
        <Btn variant="secondary" disabled={!canClear} onClick={clearLogs} title={canClear ? "" : "Clear is sim-only"}>
          Clear
        </Btn>
      </div>

      <div className="mt-4 space-y-2">
        {logs.length === 0 ? (
          <div className="text-sm text-neutral-600">No events.</div>
        ) : (
          logs.map((e) => (
            <div key={e.id} className="rounded-2xl border border-neutral-200 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium">
                  <span className="mr-2 inline-flex rounded-full bg-neutral-100 px-2 py-0.5 text-xs">{e.type}</span>
                  {e.ok ? "OK" : "FAIL"}
                </div>
                <div className="text-xs text-neutral-500">{fmt(e.ts)}</div>
              </div>
              {e.detail ? <div className="mt-1 text-sm text-neutral-600">{e.detail}</div> : null}
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
