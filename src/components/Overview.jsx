import React from "react";
import Card from "./Card";
import Badge from "./Badge";

export default function Overview({ mode, sim, status }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <Card>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Mode</div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="text-sm font-semibold text-slate-100">{mode === "device" ? "Device API" : "Simulation"}</div>
          <Badge>v1</Badge>
        </div>
      </Card>
      <Card>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Device</div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="text-sm font-semibold text-slate-100">{mode === "sim" ? sim.deviceName : status.deviceName || "(unknown)"}</div>
          <Badge>{status.online ? "Online" : "Offline"}</Badge>
        </div>
      </Card>
      <Card>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Safety</div>
        <div className="mt-2 text-sm font-semibold text-slate-200">On-device storage, no cloud DB</div>
      </Card>
    </div>
  );
}
