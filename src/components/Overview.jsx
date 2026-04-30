import React from "react";
import Card from "./Card";
import Badge from "./Badge";

export default function Overview({ mode, sim, status }) {
  return (
    <Card contentClassName="px-4 py-3.5 sm:px-5 sm:py-4">
      <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-3 text-center sm:gap-x-8">
        <div className="flex flex-wrap items-center justify-center gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Mode</span>
          <span className="text-sm font-semibold text-slate-100">{mode === "device" ? "Device API" : "Simulation"}</span>
          <Badge>v1</Badge>
        </div>
        <div className="hidden h-4 w-px bg-white/10 sm:block" aria-hidden="true" />
        <div className="flex flex-wrap items-center justify-center gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Device</span>
          <span className="max-w-[200px] truncate text-sm font-semibold text-slate-100 sm:max-w-none">
            {mode === "sim" ? sim.deviceName : status.deviceName || "(unknown)"}
          </span>
          <Badge>{status.online ? "Online" : "Offline"}</Badge>
        </div>
      </div>
    </Card>
  );
}
