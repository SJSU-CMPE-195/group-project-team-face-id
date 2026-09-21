import React from "react";
import { LogOut, Moon, RefreshCw, ShieldCheck, Sun } from "lucide-react";
import { useTheme } from "../context/useTheme.js";
import { actuatorLabels } from "../utils/actuatorStatus.js";

const titles = {
  control: "Console",
  users: "Users",
  logs: "Event log",
  settings: "Settings",
  hardware: "Hardware Simulator",
};

export default function TopBar({
  tab,
  locked,
  ignitionOn,
  online,
  simulatedActuators,
  actuatorControlAvailable,
  physicalStateConfirmed,
  busy,
  onRefresh,
  user,
  onLogout,
}) {
  const { isDark, toggleTheme } = useTheme();
  const linkLabel = online ? "Host online" : "Host offline";
  const labels = actuatorLabels({
    online,
    locked,
    ignitionOn,
    simulatedActuators,
    actuatorControlAvailable,
    physicalStateConfirmed,
  });

  return (
    <header className="app-safe-top flex min-h-16 shrink-0 items-center justify-between border-b border-white/[0.06] bg-dna-bg/90 px-4 backdrop-blur-md sm:px-5 md:min-h-14">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 shadow-lg shadow-violet-900/35 md:hidden">
          <ShieldCheck className="h-5 w-5 text-white" strokeWidth={2} />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-slate-100">{titles[tab] ?? "BASS"}</h1>
            <span className="hidden rounded-md border border-white/10 bg-dna-surface px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-500 sm:inline">
              BASS
            </span>
          </div>
          {user ? (
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500 md:hidden">
              <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-emerald-400" : "bg-rose-400"}`} />
              <span>{linkLabel}</span>
              <span aria-hidden="true">·</span>
              <span
                className={
                  simulatedActuators || physicalStateConfirmed
                    ? "text-slate-400"
                    : "text-amber-300"
                }
              >
                {online && actuatorControlAvailable
                  ? labels.lockShort
                  : labels.control}
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {user ? (
        <div className="hidden items-center gap-2 md:flex">
          <StatPill label="Link" value={online ? "Online" : "Offline"} accent={online ? "text-violet-300" : "text-rose-400"} />
          <StatPill
            label="State"
            value={labels.lockShort}
            accent={
              simulatedActuators || physicalStateConfirmed
                ? "text-slate-300"
                : "text-amber-300"
            }
          />
          <StatPill
            label="Ignition"
            value={labels.ignitionShort}
            accent={
              simulatedActuators || physicalStateConfirmed
                ? "text-slate-300"
                : "text-amber-300"
            }
          />
          <StatPill
            label="Control"
            value={labels.control}
            accent={
              online && actuatorControlAvailable
                ? "text-violet-300"
                : "text-amber-300"
            }
          />
        </div>
      ) : null}

      <div className="flex items-center gap-1">
        {user?.name ? (
          <span className="hidden max-w-32 truncate px-2 text-xs text-slate-400 sm:inline">
            {user.name}
          </span>
        ) : null}
        <button
          type="button"
          onClick={toggleTheme}
          className="flex h-12 w-12 items-center justify-center rounded-xl text-slate-500 transition hover:bg-white/[0.06] hover:text-slate-300 md:h-9 md:w-9 md:rounded-lg"
          title={isDark ? "Switch to light theme" : "Switch to dark theme"}
          aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
        >
          {isDark ? <Moon className="h-4 w-4" strokeWidth={1.75} /> : <Sun className="h-4 w-4 text-amber-500" strokeWidth={1.75} />}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onRefresh}
          className="flex h-12 w-12 items-center justify-center gap-2 rounded-xl bg-violet-600 text-sm font-semibold text-white shadow-md shadow-violet-900/30 transition hover:bg-violet-500 disabled:opacity-40 sm:w-auto sm:px-3 md:h-9 md:rounded-lg"
          title="Refresh connection and data"
          aria-label="Refresh"
        >
          <RefreshCw className={`h-4 w-4 shrink-0 ${busy ? "animate-spin" : ""}`} strokeWidth={2} />
          <span className="hidden sm:inline">Refresh</span>
        </button>
        {onLogout ? (
          <button
            type="button"
            onClick={onLogout}
            className="flex h-12 w-12 items-center justify-center rounded-xl text-slate-500 transition hover:bg-white/[0.06] hover:text-slate-300 md:h-9 md:w-9 md:rounded-lg"
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut className="h-4 w-4" strokeWidth={1.75} />
          </button>
        ) : null}
      </div>
    </header>
  );
}

function StatPill({ label, value, accent = "text-slate-200" }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-dna-surface px-3 py-1.5">
      <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`text-xs font-semibold ${accent}`}>{value}</div>
    </div>
  );
}
