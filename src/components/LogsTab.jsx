import React from "react";
import Card from "./Card";
import { fmt } from "../utils/helpers";

export default function LogsTab({ deviceLogs }) {
  const logs = deviceLogs;

  return (
    <Card>
      <div className="text-sm font-semibold text-slate-100">Event log</div>

      <div className="mt-4 space-y-2">
        {logs.length === 0 ? (
          <div className="text-sm text-slate-500">No events.</div>
        ) : (
          logs.map((e) => (
            <div key={e.id} className="rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm font-medium text-slate-100">
                  <span className="mr-2 inline-flex rounded-lg bg-violet-500/15 px-2 py-0.5 text-xs font-semibold text-violet-300">
                    {e.type}
                  </span>
                  {e.ok ? <span className="text-emerald-400">OK</span> : <span className="text-rose-400">FAIL</span>}
                </div>
                <div className="text-xs text-slate-500">{fmt(e.ts)}</div>
              </div>
              {e.detail ? <div className="mt-2 text-sm text-slate-400">{e.detail}</div> : null}
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
