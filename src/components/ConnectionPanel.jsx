import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import Switch from "./Switch";
import Input from "./Input";

export default function ConnectionPanel({ mode, setMode, baseUrl, setBaseUrl }) {
  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Connection</div>
          <div className="mt-1 text-xs text-neutral-600">Switch between Simulation and real device API.</div>
        </div>
        <Badge>LAN</Badge>
      </div>

      <div className="mt-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="text-sm">Device API mode</div>
          <Switch checked={mode === "device"} onChange={(v) => setMode(v ? "device" : "sim")} />
        </div>

        <div className={mode === "device" ? "" : "opacity-60"}>
          <div className="text-sm font-medium">Base URL</div>
          <div className="mt-2">
            <Input
              disabled={mode !== "device"}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.4.1"
            />
          </div>
          <div className="mt-2 text-xs text-neutral-500">Example: http://192.168.1.23 (your Pi)</div>
        </div>

        <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-4 text-sm">
          Demo story: Pair → Enroll → Unlock → Logs
        </div>
      </div>
    </Card>
  );
}
