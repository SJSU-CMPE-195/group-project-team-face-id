import React from "react";
import Card from "./Card";
import Btn from "./Btn";

export default function ControlTab({ busy, doUnlock, setTab }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <Card>
        <div className="text-sm font-semibold">Face scan</div>
        <div className="mt-1 text-xs text-neutral-600">v1 placeholder. Later: show camera preview + recognition result.</div>
        <div className="mt-4 rounded-2xl border border-neutral-200 bg-neutral-50 p-4 text-sm">
          Camera area (UI only)
        </div>
      </Card>

      <Card>
        <div className="text-sm font-semibold">Quick actions</div>
        <div className="mt-3 grid gap-2">
          <Btn disabled={busy} onClick={doUnlock}>Unlock</Btn>
          <Btn variant="secondary" disabled={busy} onClick={() => setTab("users")}>Go to Users</Btn>
          <Btn variant="secondary" disabled={busy} onClick={() => setTab("logs")}>Go to Logs</Btn>
        </div>
      </Card>
    </div>
  );
}
