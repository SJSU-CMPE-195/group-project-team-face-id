import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import Switch from "./Switch";
import Input from "./Input";

export default function ConnectionPanel({ mode, setMode, baseUrl, setBaseUrl, faceApiUrl, setFaceApiUrl }) {
  return (
    <Card>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-slate-100">Connection</div>
          <div className="mt-1 text-xs text-slate-400">
            Simulation vs Pi device API (<span className="font-mono text-[11px]">pi_device_api.py</span>), plus local Face
            API for browser scan.
          </div>
        </div>
        <Badge>LAN</Badge>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2 lg:gap-6">
        <div className="rounded-xl border border-white/[0.06] bg-dna-bg/50 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm font-medium text-slate-200">Device API mode</span>
            <Switch checked={mode === "device"} onChange={(v) => setMode(v ? "device" : "sim")} />
          </div>
          <div className={`mt-4 space-y-2 ${mode === "device" ? "" : "opacity-50"}`}>
            <label className="text-xs font-medium uppercase tracking-wider text-slate-500">Base URL</label>
            <Input
              disabled={mode !== "device"}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.4.1:5000"
            />
            <p className="text-xs text-slate-500">Pi IP and Flask port (default 5000).</p>
          </div>
        </div>

        <div className="rounded-xl border border-white/[0.06] bg-dna-bg/50 p-4">
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500">Face API (InsightFace)</div>
          <p className="mt-2 text-xs leading-relaxed text-slate-400">
            Browser camera posts frames here. Run from <span className="font-mono text-[11px] text-slate-500">car_face_auth</span>:{" "}
            <code className="block mt-1 break-all rounded-lg bg-black/30 px-2 py-1.5 font-mono text-[10px] text-slate-400">
              python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765
            </code>
          </p>
          <div className="mt-3">
            <Input value={faceApiUrl} onChange={(e) => setFaceApiUrl(e.target.value)} placeholder="http://127.0.0.1:8765" />
          </div>
        </div>
      </div>

      <div className="mt-4 rounded-xl border border-violet-500/20 bg-violet-500/[0.07] px-4 py-3 text-sm text-slate-300">
        <span className="font-medium text-violet-300/90">Flow:</span> pair → enroll → unlock → logs
      </div>
    </Card>
  );
}
