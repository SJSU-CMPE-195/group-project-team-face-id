import React from "react";
import Card from "./Card";
import Badge from "./Badge";

export default function Overview({ mode, sim, status }) {
  return (
    <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
      <Card>
        <div className="text-xs text-neutral-500">Mode</div>
        <div className="mt-1 flex items-center justify-between">
          <div className="text-sm font-medium">{mode === "device" ? "Device API" : "Simulation"}</div>
          <Badge>v1</Badge>
        </div>
      </Card>
      <Card>
        <div className="text-xs text-neutral-500">Device</div>
        <div className="mt-1 flex items-center justify-between">
          <div className="text-sm font-medium">{mode === "sim" ? sim.deviceName : status.deviceName || "(unknown)"}</div>
          <Badge>{status.online ? "Online" : "Offline"}</Badge>
        </div>
      </Card>
      <Card>
        <div className="text-xs text-neutral-500">Safety</div>
        <div className="mt-1 text-sm font-medium">Local-only, no cloud</div>
      </Card>
    </div>
  );
}
