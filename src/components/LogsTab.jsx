import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import { fmt } from "../utils/helpers";

const OUTCOMES = {
  success: { label: "Success", variant: "ok" },
  denied: { label: "Denied", variant: "err" },
  cancelled: { label: "Cancelled", variant: "warn" },
  error: { label: "Failed", variant: "err" },
  info: { label: "Info", variant: "info" },
};

const LEGACY_TITLES = {
  access_change: "Face access updated",
  delete_user: "User removed",
  enroll: "User added",
  enroll_cancelled: "Face enrollment cancelled",
  enroll_embedding: "Face template updated",
  enrollment: "Face enrollment",
  enrollment_started: "Face enrollment started",
  face_scan: "Face scan",
  ignition: "Ignition control",
  lock: "Lock command",
  operation_authorization: "Operation approval",
  scan_started: "Face scan started",
  scan_timing: "Face scan diagnostics",
  session_login: "Account sign-in",
  settings: "Settings updated",
  simulated_actuator: "Simulator control",
  unlock: "Unlock command",
};

function readableLog(entry) {
  const suppliedOutcome =
    typeof entry.outcome === "string" && OUTCOMES[entry.outcome]
      ? entry.outcome
      : null;
  const cancelled =
    entry.type === "enroll_cancelled" || entry.type === "scan_cancelled";
  const outcome =
    suppliedOutcome ||
    (cancelled ? "cancelled" : entry.ok ? "success" : "error");
  const title =
    typeof entry.title === "string" && entry.title.trim()
      ? entry.title.trim()
      : LEGACY_TITLES[entry.type] || "Device activity";
  const summary =
    typeof entry.summary === "string" && entry.summary.trim()
      ? entry.summary.trim()
      : outcome === "success"
        ? "The operation completed successfully."
        : outcome === "denied"
          ? "The operation was denied."
          : outcome === "cancelled"
            ? "The operation was cancelled."
            : outcome === "info"
              ? "The device recorded this activity."
              : "The operation did not complete.";
  return { outcome, summary, title };
}

export default function LogsTab({ deviceLogs }) {
  const logs = deviceLogs;

  return (
    <Card>
      <div className="text-sm font-semibold text-slate-100">Event log</div>

      <div className="mt-4 space-y-2">
        {logs.length === 0 ? (
          <div className="text-sm text-slate-500">No events.</div>
        ) : (
          logs.map((entry) => {
            const display = readableLog(entry);
            const outcome = OUTCOMES[display.outcome];
            return (
              <div
                key={entry.id}
                className="rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-100">
                      {display.title}
                    </div>
                    <div className="mt-1 text-sm text-slate-400">
                      {display.summary}
                    </div>
                  </div>
                  <Badge variant={outcome.variant}>{outcome.label}</Badge>
                </div>
                <div className="mt-2 text-xs text-slate-500">
                  {fmt(entry.ts)}
                </div>
              </div>
            );
          })
        )}
      </div>
    </Card>
  );
}
