import React from "react";

export default function TabBtn({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-xl px-3.5 py-2 text-sm font-semibold ring-1 transition ${
        active
          ? "bg-teal-500 text-slate-950 ring-teal-400 shadow-[0_0_16px_-4px_rgba(45,212,191,0.5)]"
          : "border border-transparent bg-slate-800/70 text-slate-300 ring-slate-600/60 hover:bg-slate-800 hover:text-slate-100"
      }`}
    >
      {children}
    </button>
  );
}
