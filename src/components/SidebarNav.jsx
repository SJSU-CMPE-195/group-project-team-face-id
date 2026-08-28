import React from "react";
import { LayoutDashboard, Users, ScrollText, Settings, ShieldCheck } from "lucide-react";

const items = [
  { id: "control", icon: LayoutDashboard, label: "Console" },
  { id: "users", icon: Users, label: "Users" },
  { id: "logs", icon: ScrollText, label: "Logs" },
  { id: "settings", icon: Settings, label: "Settings" },
];

export default function SidebarNav({ tab, setTab }) {
  return (
    <>
      <aside className="hidden w-[72px] shrink-0 flex-col items-center border-r border-white/[0.06] bg-dna-sidebar py-5 md:flex">
        <div
          className="mb-6 flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 shadow-lg shadow-violet-900/40"
          title="BASS"
        >
          <ShieldCheck className="h-5 w-5 text-white" strokeWidth={2} />
        </div>

        <nav className="flex flex-1 flex-col gap-1.5" aria-label="Main navigation">
          <NavigationItems tab={tab} setTab={setTab} />
        </nav>

        <div className="mt-auto h-9 w-9 rounded-full border border-white/10 bg-dna-surface" title="Profile" />
      </aside>

      <nav className="mobile-bottom-nav fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t border-white/[0.08] bg-dna-sidebar/95 px-2 backdrop-blur-xl md:hidden" aria-label="Main navigation">
        <NavigationItems tab={tab} setTab={setTab} mobile />
      </nav>
    </>
  );
}

function NavigationItems({ tab, setTab, mobile = false }) {
  return items.map(({ id, icon, label }) => {
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
        className={
          mobile
            ? `relative flex min-h-16 flex-col items-center justify-center gap-1 rounded-2xl text-[10px] font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300/80 ${active ? "text-fuchsia-300" : "text-slate-500 active:bg-white/[0.06]"}`
            : `flex h-11 w-11 items-center justify-center rounded-xl transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300/80 ${
                active
                  ? "bg-fuchsia-600 text-white shadow-[0_0_20px_-4px_rgba(217,70,239,0.65)]"
                  : "text-slate-500 hover:bg-white/[0.06] hover:text-slate-300"
              }`
        }
      >
        <span className={mobile && active ? "flex h-8 w-14 items-center justify-center rounded-full bg-fuchsia-500/15" : mobile ? "flex h-8 w-14 items-center justify-center" : ""}>
          <NavIcon className="h-5 w-5" strokeWidth={active ? 2.25 : 1.75} />
        </span>
        {mobile ? <span>{label}</span> : null}
      </button>
    );
  });
}
