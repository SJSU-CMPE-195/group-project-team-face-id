import React from "react";
import { Moon, RefreshCw, Sun } from "lucide-react";
import { useTheme } from "../context/useTheme.js";

const titles = {
  control: "Console",
  users: "Users",
  logs: "Event log",
  settings: "Settings",
};

export default function TopBar({ tab, mode, locked, ignitionOn, online, busy, onRefresh }) {
  const { isDark, toggleTheme } = useTheme();
  const modeLabel = mode === "device" ? "Device API" : "Simulation";
  const lockLabel = locked ? "Locked" : "Unlocked";

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-white/[0.06] bg-dna-bg/90 px-5 backdrop-blur-md">
      <div className="flex min-w-0 items-center gap-3">
        <h1 className="truncate text-sm font-semibold text-slate-100 md:text-base">{titles[tab] ?? "BASS"}</h1>
        <span className="hidden rounded-md border border-white/10 bg-dna-surface px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-500 sm:inline">
          BASS
        </span>
      </div>

      <div className="hidden items-center gap-2 md:flex">
        <StatPill label="Mode" value={modeLabel} />
        <StatPill label="Link" value={online ? "Online" : "Offline"} accent={online ? "text-violet-300" : "text-rose-400"} />
        <StatPill label="State" value={lockLabel} accent={locked ? "text-fuchsia-300" : "text-emerald-300"} />
        <StatPill label="Ignition" value={ignitionOn ? "On" : "Off"} accent={ignitionOn ? "text-amber-300" : "text-slate-300"} />
      </div>

      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={toggleTheme}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition hover:bg-white/[0.06] hover:text-slate-300"
          title={isDark ? "Switch to light theme" : "Switch to dark theme"}
          aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
        >
          {isDark ? <Moon className="h-4 w-4" strokeWidth={1.75} /> : <Sun className="h-4 w-4 text-amber-500" strokeWidth={1.75} />}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onRefresh}
          className="flex h-9 items-center gap-2 rounded-lg bg-violet-600 px-3 text-sm font-semibold text-white shadow-md shadow-violet-900/30 transition hover:bg-violet-500 disabled:opacity-40"
          title="Refresh connection and data"
          aria-label="Refresh"
        >
          <RefreshCw className={`h-4 w-4 shrink-0 ${busy ? "animate-spin" : ""}`} strokeWidth={2} />
          <span className="hidden sm:inline">Refresh</span>
        </button>
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
