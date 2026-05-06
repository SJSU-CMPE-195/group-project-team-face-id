import React from "react";
import Card from "./Card";
import Input from "./Input";
import Switch from "./Switch";
import Btn from "./Btn";

export default function SettingsTab({ settings, setSettings, busy, saveSettings }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <Card>
        <div className="text-sm font-semibold text-slate-100">Core settings</div>

        <div className="mt-4 space-y-4">
          <div>
            <div className="text-sm font-medium text-slate-200">Auto re-lock (seconds)</div>
            <div className="mt-2">
              <Input
                type="number"
                value={settings.autoRelockSeconds}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, autoRelockSeconds: Math.max(0, Math.min(600, parseInt(e.target.value || "0", 10))) }))
                }
              />
            </div>
            <div className="mt-1 text-xs text-slate-500">0 = disabled. Typical 5–15s.</div>
          </div>

          <div>
            <div className="text-sm font-medium text-slate-200">Ignition auto-stop (seconds)</div>
            <div className="mt-2">
              <Input
                type="number"
                value={settings.ignitionAutoStopSeconds}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    ignitionAutoStopSeconds: Math.max(0, Math.min(1800, parseInt(e.target.value || "0", 10))),
                  }))
                }
              />
            </div>
            <div className="mt-1 text-xs text-slate-500">0 = disabled. Typical 15–60s.</div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-slate-200">Liveness detection</div>
              <div className="text-xs text-slate-500">Reduces simple photo spoofing (v1 simplified).</div>
            </div>
            <Switch checked={settings.liveness} onChange={(v) => setSettings((s) => ({ ...s, liveness: v }))} />
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-slate-200">Fail lockout</div>
              <div className="text-xs text-slate-500">Cooldown after repeated failures.</div>
            </div>
            <Switch checked={settings.failLockout} onChange={(v) => setSettings((s) => ({ ...s, failLockout: v }))} />
          </div>

          <div>
            <div className="text-sm font-medium text-slate-200">Lockout after (failures)</div>
            <div className="mt-2">
              <Input
                type="number"
                value={settings.lockoutAfter}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, lockoutAfter: Math.max(1, Math.min(20, parseInt(e.target.value || "5", 10))) }))
                }
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
            <Btn
              variant="secondary"
              onClick={() =>
                setSettings({
                  autoRelockSeconds: 10,
                  ignitionAutoStopSeconds: 20,
                  liveness: true,
                  failLockout: true,
                  lockoutAfter: 5,
                })
              }
            >
              Reset
            </Btn>
            <Btn disabled={busy} onClick={saveSettings}>
              Save
            </Btn>
          </div>
        </div>
      </Card>

      <Card>
        <div className="text-sm font-semibold text-slate-100">Notes</div>
        <div className="mt-3 space-y-3 text-sm text-slate-400">
          <div className="rounded-xl border border-white/[0.06] bg-dna-bg p-4">
            <div className="text-xs font-medium uppercase tracking-wider text-violet-400/90">Next</div>
            <div className="mt-1 text-slate-300">Extend Pi services; keep the same `/api/*` contract.</div>
          </div>
          <div className="rounded-xl border border-white/[0.06] bg-dna-bg p-4">
            <div className="text-xs font-medium uppercase tracking-wider text-fuchsia-400/90">Security</div>
            <div className="mt-1 text-slate-300">LAN-only prototype; add pairing / TLS later.</div>
          </div>
        </div>
      </Card>
    </div>
  );
}
