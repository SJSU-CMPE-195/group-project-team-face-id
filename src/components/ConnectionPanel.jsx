import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import Switch from "./Switch";
import Input from "./Input";

export default function ConnectionPanel({ mode, setMode, baseUrl, setBaseUrl, faceApiUrl, setFaceApiUrl }) {
  return (
    <Card>
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-slate-100">Connection</div>
          <div className="mt-1 text-xs text-slate-400">Simulation vs Pi HTTP API (`pi_device_api.py`).</div>
        </div>
        <Badge>LAN</Badge>
      </div>

      <div className="mt-4 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm text-slate-200">Device API mode</div>
          <Switch checked={mode === "device"} onChange={(v) => setMode(v ? "device" : "sim")} />
        </div>

        <div className={mode === "device" ? "" : "opacity-50"}>
          <div className="text-sm font-medium text-slate-200">Base URL</div>
          <div className="mt-2">
            <Input
              disabled={mode !== "device"}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.4.1:5000"
            />
          </div>
          <div className="mt-2 text-xs text-slate-500">Pi IP + Flask port (default 5000).</div>
        </div>

        <div>
          <div className="text-sm font-medium text-slate-200">Face API (InsightFace)</div>
          <div className="mt-1 text-xs text-slate-500">
            Browser camera sends frames here. From <span className="font-mono text-[11px] text-slate-400">car_face_auth</span> run:{" "}
            <span className="font-mono text-[11px] text-slate-400">
              python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765
            </span>
          </div>
          <div className="mt-2">
            <Input
              value={faceApiUrl}
              onChange={(e) => setFaceApiUrl(e.target.value)}
              placeholder="http://127.0.0.1:8765"
            />
          </div>
        </div>

        <div className="rounded-xl border border-violet-500/20 bg-violet-500/[0.07] p-4 text-sm text-slate-300">
          <span className="font-medium text-violet-300/90">Flow:</span> pair → enroll → unlock → logs
        </div>
      </div>
    </Card>
  );
}
