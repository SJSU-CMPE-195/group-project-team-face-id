import React from "react";

export default function Toast({ toast }) {
  if (!toast) return null;

  const ringClass =
    toast.type === "err"
      ? "ring-red-200"
      : toast.type === "ok"
      ? "ring-emerald-200"
      : "ring-neutral-200";

  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2">
      <div className={`rounded-2xl px-4 py-3 shadow-lg ring-1 ${ringClass}`}>
        <div className="text-sm font-semibold">{toast.title}</div>
        {toast.msg ? <div className="mt-1 text-xs text-neutral-600">{toast.msg}</div> : null}
      </div>
    </div>
  );
}
