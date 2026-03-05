import React from "react";
import Card from "./Card";
import Input from "./Input";
import Switch from "./Switch";
import Btn from "./Btn";

export default function SettingsTab({ mode, settings, setSettings, busy, saveSettings }) {
  if (mode === "device") {
    return (
      <Card>
        <div className="text-sm font-semibold">Settings (Device API mode)</div>
        <div className="mt-2 text-sm text-neutral-600">
          Implement <code>POST /api/settings</code> on Pi. Validate on device.
        </div>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <Card>
        <div className="text-sm font-semibold">Core settings</div>

        <div className="mt-4 space-y-4">
          <div>
            <div className="text-sm font-medium">Auto re-lock (seconds)</div>
            <div className="mt-2">
              <Input
                type="number"
                value={settings.autoRelockSeconds}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, autoRelockSeconds: Math.max(0, Math.min(600, parseInt(e.target.value || "0", 10))) }))
                }
              />
            </div>
            <div className="mt-1 text-xs text-neutral-500">0 = disabled. Recommended 5–15.</div>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">Liveness detection</div>
              <div className="text-xs text-neutral-600">Reduce photo spoofing (simplified in v1).</div>
            </div>
            <Switch checked={settings.liveness} onChange={(v) => setSettings((s) => ({ ...s, liveness: v }))} />
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">Fail lockout</div>
              <div className="text-xs text-neutral-600">Temporary lock after repeated failures.</div>
            </div>
            <Switch checked={settings.failLockout} onChange={(v) => setSettings((s) => ({ ...s, failLockout: v }))} />
          </div>

          <div>
            <div className="text-sm font-medium">Lockout after (failures)</div>
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

          <div className="flex items-center justify-end gap-2">
            <Btn
              variant="secondary"
              onClick={() => setSettings({ autoRelockSeconds: 10, liveness: true, failLockout: true, lockoutAfter: 5 })}
            >
              Reset
            </Btn>
            <Btn disabled={busy} onClick={saveSettings}>Save</Btn>
          </div>
        </div>
      </Card>

      <Card>
        <div className="text-sm font-semibold">Demo notes</div>
        <div className="mt-2 text-sm text-neutral-700 space-y-3">
          <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-4">
            <div className="text-xs text-neutral-500">Next step</div>
            <div className="mt-1">Implement Pi endpoints, then switch to Device API mode.</div>
          </div>
          <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-4">
            <div className="text-xs text-neutral-500">Security</div>
            <div className="mt-1">Local network only, add pairing PIN in v2.</div>
          </div>
        </div>
      </Card>
    </div>
  );
}
