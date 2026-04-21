import React from "react";

export default function Toast({ toast }) {
  if (!toast) return null;

  const ringClass =
    toast.type === "err"
      ? "border-rose-500/40 ring-1 ring-rose-500/30"
      : toast.type === "ok"
      ? "border-violet-500/40 ring-1 ring-violet-500/25"
      : "border-white/10 ring-1 ring-white/10";

  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 px-4">
      <div className={`rounded-xl bg-dna-surface px-4 py-3 shadow-panel backdrop-blur-md ${ringClass}`}>
        <div className="text-sm font-semibold text-slate-50">{toast.title}</div>
        {toast.msg ? <div className="mt-1 text-xs text-slate-400">{toast.msg}</div> : null}
      </div>
    </div>
  );
}
