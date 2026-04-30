import React from "react";
import Card from "./Card";
import Badge from "./Badge";

export default function Overview({ mode, sim, status }) {
  return (
    <Card contentClassName="px-4 py-3.5 sm:px-5 sm:py-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-6 gap-y-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Mode</span>
            <span className="truncate text-sm font-semibold text-slate-100">
              {mode === "device" ? "Device API" : "Simulation"}
            </span>
            <Badge>v1</Badge>
          </div>
          <div className="hidden h-4 w-px bg-white/10 sm:block" aria-hidden="true" />
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Device</span>
            <span className="truncate text-sm font-semibold text-slate-100">
              {mode === "sim" ? sim.deviceName : status.deviceName || "(unknown)"}
            </span>
            <Badge>{status.online ? "Online" : "Offline"}</Badge>
          </div>
        </div>
      </div>
    </Card>
  );
}
