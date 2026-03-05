import React from "react";
import Btn from "./Btn";

export default function Header({ busy, onRefresh }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Face Unlock Car Lock</h1>
        <p className="mt-2 max-w-2xl text-sm text-neutral-600">
          v1 control panel: connect, enroll users, unlock, view logs, adjust settings. Switch to Device API mode when your Pi is ready.
        </p>
      </div>
      <Btn variant="secondary" disabled={busy} onClick={onRefresh}>
        {busy ? "..." : "Refresh"}
      </Btn>
    </div>
  );
}
