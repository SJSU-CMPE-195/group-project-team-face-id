import React from "react";

export default function Toast({ toast }) {
  if (!toast) return null;

  const ringClass =
    toast.type === "err"
      ? "border-rose-500/40 ring-1 ring-rose-500/30"
      : toast.type === "ok"
      ? "border-violet-500/40 ring-1 ring-violet-500/25"
      : "border-white/10 ring-1 ring-white/10";

  const titleClass = toast.type === "err" ? "text-rose-100" : "text-slate-50";
  const msgClass = toast.type === "err" ? "text-rose-200/90" : "text-slate-400";

  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 px-4">
      <div className={`rounded-xl bg-dna-surface px-4 py-3 shadow-panel backdrop-blur-md ${ringClass}`}>
        <div className={`text-sm font-semibold ${titleClass}`}>{toast.title}</div>
        {toast.msg ? <div className={`mt-1 text-xs ${msgClass}`}>{toast.msg}</div> : null}
      </div>
    </div>
  );
}
