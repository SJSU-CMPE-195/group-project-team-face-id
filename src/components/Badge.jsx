import React from "react";

const VARIANTS = {
  default: "border-violet-500/30 bg-violet-500/10 text-violet-200/95",
  ok:      "border-emerald-500/30 bg-emerald-500/10 text-emerald-300/95",
  err:     "border-rose-500/30 bg-rose-500/10 text-rose-300/95",
  warn:    "border-amber-500/30 bg-amber-500/10 text-amber-300/95",
  info:    "border-sky-500/30 bg-sky-500/10 text-sky-300/95",
};

export default function Badge({ children, variant = "default" }) {
  const cls = VARIANTS[variant] ?? VARIANTS.default;
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${cls}`}>
      {children}
    </span>
  );
}
