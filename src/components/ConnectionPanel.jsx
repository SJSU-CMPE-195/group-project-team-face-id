import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import Switch from "./Switch";
import Input from "./Input";

export default function ConnectionPanel({ mode, setMode, baseUrl, setBaseUrl, faceApiUrl, setFaceApiUrl }) {
  return (
    <Card contentClassName="p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Connection</h2>
          <p className="mt-1 max-w-xl text-sm text-slate-400">
            Pi runs <span className="font-mono text-xs text-slate-500">pi_device_api.py</span>. This browser uses a separate Face API for the camera
            above.
          </p>
        </div>
        <Badge>LAN</Badge>
      </div>

      <div className="mt-6 grid gap-5 md:grid-cols-2 md:gap-6">
        <div className="rounded-xl border border-white/[0.08] bg-dna-bg/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm font-medium text-slate-200">Device API</span>
            <Switch checked={mode === "device"} onChange={(v) => setMode(v ? "device" : "sim")} />
          </div>
          <div className={`mt-4 space-y-2 ${mode === "device" ? "" : "pointer-events-none opacity-45"}`}>
            <label className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Base URL</label>
            <Input
              disabled={mode !== "device"}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.4.1:5000"
            />
            <p className="text-xs text-slate-500">Pi address and port (default 5000).</p>
          </div>
        </div>

        <div className="rounded-xl border border-white/[0.08] bg-dna-bg/40 p-4">
          <div className="text-sm font-medium text-slate-200">Face API</div>
          <p className="mt-1 text-xs text-slate-500">InsightFace endpoint for enroll / verify from this page.</p>
          <div className="mt-3">
            <label className="sr-only" htmlFor="face-api-url">
              Face API URL
            </label>
            <Input
              id="face-api-url"
              value={faceApiUrl}
              onChange={(e) => setFaceApiUrl(e.target.value)}
              placeholder="http://127.0.0.1:8765"
            />
          </div>
          <details className="mt-3 rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2 text-xs text-slate-400">
            <summary className="cursor-pointer select-none text-slate-300 outline-none hover:text-slate-200">
              Run Face API locally
            </summary>
            <p className="mt-2 text-slate-500">
              From <span className="font-mono text-[11px] text-slate-500">car_face_auth</span> with venv active:
            </p>
            <code className="mt-1 block break-all rounded-md bg-black/40 px-2 py-2 font-mono text-[10px] leading-relaxed text-slate-400">
              python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765
            </code>
          </details>
        </div>
      </div>

      <p className="mt-5 border-t border-white/[0.06] pt-4 text-center text-xs text-slate-500">
        <span className="text-violet-400/90">Flow</span>
        <span className="mx-1.5 text-slate-600">·</span>
        pair → enroll → unlock → logs
      </p>
    </Card>
  );
}
