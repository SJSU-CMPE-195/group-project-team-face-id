import React from "react";
import { LayoutDashboard, Users, ScrollText, Settings, Cpu } from "lucide-react";

const items = [
  { id: "control", icon: LayoutDashboard, label: "Console" },
  { id: "users", icon: Users, label: "Users" },
  { id: "logs", icon: ScrollText, label: "Logs" },
  { id: "settings", icon: Settings, label: "Settings" },
];

export default function SidebarNav({ tab, setTab }) {
  return (
    <aside className="flex w-[72px] shrink-0 flex-col items-center border-r border-white/[0.06] bg-dna-sidebar py-5">
      <div
        className="mb-6 flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 shadow-lg shadow-violet-900/40"
        title="BASS"
      >
        <Cpu className="h-5 w-5 text-white" strokeWidth={2} />
      </div>

      <nav className="flex flex-1 flex-col gap-1.5" aria-label="Main">
        {items.map(({ id, icon, label }) => {
          const active = tab === id;
          const NavIcon = icon;
          return (
            <button
              key={id}
              type="button"
              title={label}
              aria-label={label}
              aria-current={active ? "page" : undefined}
              onClick={() => setTab(id)}
              className={`flex h-11 w-11 items-center justify-center rounded-xl transition ${
                active
                  ? "bg-fuchsia-600 text-white shadow-[0_0_20px_-4px_rgba(217,70,239,0.65)]"
                  : "text-slate-500 hover:bg-white/[0.06] hover:text-slate-300"
              }`}
            >
              <NavIcon className="h-5 w-5" strokeWidth={active ? 2.25 : 1.75} />
            </button>
          );
        })}
      </nav>

      <div className="mt-auto h-9 w-9 rounded-full border border-white/10 bg-dna-surface" title="Profile" />
    </aside>
  );
}
