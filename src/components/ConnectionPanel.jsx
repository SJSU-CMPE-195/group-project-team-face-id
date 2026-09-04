import React from "react";
import { Download, LogOut } from "lucide-react";
import Card from "./Card";
import Badge from "./Badge";
import Btn from "./Btn";
import Switch from "./Switch";
import Input from "./Input";
import usePwaInstall from "../hooks/usePwaInstall";

// The in-browser Fake Pi never reaches the network, so it needs no token.
function isMockPiUrl(url) {
  return (url || "").trim().replace(/\/$/, "").toLocaleLowerCase() === "fake://pi";
}

export default function ConnectionPanel({ mode, setMode, baseUrl, setBaseUrl, me, onSignOut, faceApiUrl, setFaceApiUrl }) {
  const pwa = usePwaInstall();
  const httpsPage = typeof window !== "undefined" && window.location.protocol === "https:";
  const insecureDeviceApi = httpsPage && mode === "device" && /^http:\/\//i.test(baseUrl.trim());
  const insecureFaceApi = httpsPage && /^http:\/\//i.test(faceApiUrl.trim());
  const installMessage = pwa.installed
    ? "Installed. BASS now launches in its own app window."
    : !pwa.secureContext
      ? "Installation needs trusted HTTPS or Android USB port forwarding to localhost."
      : pwa.outcome === "dismissed"
        ? "Installation was dismissed. You can retry from Chrome's menu."
        : pwa.canInstall
          ? "Chrome has verified this build and it is ready to install."
          : "Use Chrome's Install app menu after interacting with the page for a short time.";

  return (
    <Card contentClassName="p-5 sm:p-6">
      <div className="flex flex-col items-center text-center">
        <div className="flex flex-wrap items-center justify-center gap-2">
          <h2 className="text-base font-semibold text-slate-100">Connection</h2>
          <Badge>LAN</Badge>
        </div>
        <p className="mt-2 max-w-xl text-sm text-slate-400">
          Pi runs <span className="font-mono text-xs text-slate-500">pi_device_api.py</span>. Use <span className="font-mono text-xs text-slate-500">fake://pi</span> for the in-browser Fake Pi.
        </p>
      </div>

      <div className="mx-auto mt-6 grid w-full max-w-3xl gap-5 md:grid-cols-2 md:gap-6">
        <div className="rounded-xl border border-white/[0.08] bg-dna-bg/40 p-4 text-center">
          <div className="flex items-center justify-center gap-3">
            <span className="text-sm font-medium text-slate-200">Device API</span>
            <Switch ariaLabel="Device API mode" checked={mode === "device"} onChange={(v) => setMode(v ? "device" : "sim")} />
          </div>
          <div className={`mx-auto mt-4 max-w-md space-y-2 text-center ${mode === "device" ? "" : "pointer-events-none opacity-45"}`}>
            <label htmlFor="base-url" className="block text-[11px] font-medium uppercase tracking-wider text-slate-500">Base URL</label>
            <Input
              id="base-url"
              disabled={mode !== "device"}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.4.1:5000"
            />
            <p className="text-xs text-slate-500">
              Leave blank when the Pi serves this app (same origin). Use fake://pi
              for the in-browser fake, or an HTTP URL for a mock server.
            </p>

            {mode === "device" && !isMockPiUrl(baseUrl) && (
              <div className="mt-4 rounded-lg border border-white/[0.08] bg-black/20 px-3 py-3 text-left">
                <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
                  Signed in as
                </div>
                {me ? (
                  <>
                    <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-200">{me.name}</span>
                      <Badge variant={me.role === "ADMIN" ? "ok" : "info"}>{me.role}</Badge>
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-slate-500">
                      This browser holds a session cookie, not a shared key. Nothing
                      secret is stored where a script can read it.
                    </p>
                    <Btn onClick={onSignOut} className="mt-3 w-full gap-2">
                      <LogOut aria-hidden="true" size={16} />
                      Sign out
                    </Btn>
                  </>
                ) : (
                  <p className="mt-1 text-xs text-slate-500">Not paired yet.</p>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-white/[0.08] bg-dna-bg/40 p-4 text-center">
          <div className="text-sm font-medium text-slate-200">Face API</div>
          <p className="mt-1 text-xs text-slate-500">InsightFace endpoint for enroll / verify from this page.</p>
          <div className="mx-auto mt-3 max-w-md">
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
          <details className="mx-auto mt-3 max-w-md rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2 text-left text-xs text-slate-400">
            <summary className="cursor-pointer list-none text-center text-slate-300 outline-none hover:text-slate-200 [&::-webkit-details-marker]:hidden">
              Run Face API locally
            </summary>
            <p className="mt-2 text-center text-slate-500">
              From <span className="font-mono text-[11px] text-slate-500">car_face_auth</span> with venv active:
            </p>
            <code className="mt-1 block break-all rounded-md bg-black/40 px-2 py-2 text-center font-mono text-[10px] leading-relaxed text-slate-400">
              python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765
            </code>
          </details>
        </div>
      </div>

      <div className="mx-auto mt-5 flex w-full max-w-3xl flex-col items-center rounded-xl border border-white/[0.08] bg-dna-bg/40 p-4 text-center">
        <div className="flex flex-wrap items-center justify-center gap-2">
          <span className="text-sm font-medium text-slate-200">Install on this device</span>
          <Badge variant={pwa.installed ? "ok" : pwa.secureContext ? "info" : "warn"}>
            {pwa.installed ? "Installed" : pwa.secureContext ? "PWA" : "HTTPS required"}
          </Badge>
        </div>
        <p className="mt-2 max-w-xl text-xs leading-relaxed text-slate-500">{installMessage}</p>
        {pwa.canInstall && (
          <Btn onClick={() => void pwa.install()} className="mt-4 w-full gap-2 sm:w-auto">
            <Download aria-hidden="true" size={16} />
            Install BASS
          </Btn>
        )}
        {(insecureDeviceApi || insecureFaceApi) && (
          <p className="mt-3 max-w-xl text-xs leading-relaxed text-amber-300/90">
            An HTTPS-installed app cannot call an HTTP {insecureDeviceApi && insecureFaceApi ? "Device or Face API" : insecureDeviceApi ? "Device API" : "Face API"}. Use a same-origin HTTPS proxy or HTTPS endpoints.
          </p>
        )}
      </div>

      <p className="mt-5 border-t border-white/[0.06] pt-4 text-center text-xs text-slate-500">
        <span className="text-violet-400/90">Flow</span>
        <span className="mx-1.5 text-slate-600">·</span>
        pair → enroll → unlock → logs
      </p>
    </Card>
  );
}
