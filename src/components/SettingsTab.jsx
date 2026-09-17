import React from "react";
import Card from "./Card";
import Input from "./Input";
import Switch from "./Switch";
import Btn from "./Btn";
import DevicePairingCard from "./DevicePairingCard";

export default function SettingsTab({ settings, setSettings, busy, saveSettings }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <Card>
        <div className="text-sm font-semibold text-slate-100">Core settings</div>

        <div className="mt-4 space-y-4">
          <div>
            <label htmlFor="auto-relock-seconds" className="text-sm font-medium text-slate-200">Auto re-lock (seconds)</label>
            <div className="mt-2">
              <Input
                id="auto-relock-seconds"
                type="number"
                min="0"
                max="600"
                value={settings.autoRelockSeconds}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, autoRelockSeconds: Math.max(0, Math.min(600, parseInt(e.target.value || "0", 10))) }))
                }
              />
            </div>
            <div className="mt-1 text-xs text-slate-500">0 = disabled.</div>
          </div>

          <div>
            <label htmlFor="ignition-auto-stop-seconds" className="text-sm font-medium text-slate-200">Ignition auto-stop (seconds)</label>
            <div className="mt-2">
              <Input
                id="ignition-auto-stop-seconds"
                type="number"
                min="0"
                max="1800"
                value={settings.ignitionAutoStopSeconds}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    ignitionAutoStopSeconds: Math.max(0, Math.min(1800, parseInt(e.target.value || "0", 10))),
                  }))
                }
              />
            </div>
            <div className="mt-1 text-xs text-slate-500">0 = disabled.</div>
          </div>

          <div>
            <label htmlFor="prompt-auto-lock-seconds" className="text-sm font-medium text-slate-200">Ignition prompt auto-lock (seconds)</label>
            <div className="mt-2">
              <Input
                id="prompt-auto-lock-seconds"
                type="number"
                min="0"
                max="600"
                value={typeof settings.promptAutoLockSeconds === "number" ? settings.promptAutoLockSeconds : 0}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    promptAutoLockSeconds: Math.max(0, Math.min(600, parseInt(e.target.value || "0", 10))),
                  }))
                }
              />
            </div>
            <div className="mt-1 text-xs text-slate-500">
              Auto-lock if no ignition choice. 0 = disabled.
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-medium text-slate-200">Liveness detection</div>
            <Switch ariaLabel="Liveness detection" checked={settings.liveness} onChange={(v) => setSettings((s) => ({ ...s, liveness: v }))} />
          </div>

          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-medium text-slate-200">Fail lockout</div>
            <Switch ariaLabel="Fail lockout" checked={settings.failLockout} onChange={(v) => setSettings((s) => ({ ...s, failLockout: v }))} />
          </div>

          <div>
            <label htmlFor="lockout-after-failures" className="text-sm font-medium text-slate-200">Lockout after (failures)</label>
            <div className="mt-2">
              <Input
                id="lockout-after-failures"
                type="number"
                min="1"
                max="20"
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
                  promptAutoLockSeconds: 0,
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

      <DevicePairingCard />
    </div>
  );
}
