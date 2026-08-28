import React from "react";

export default function Btn({ variant = "primary", disabled, onClick, children, title, className = "" }) {
  const base =
    "inline-flex min-h-12 items-center justify-center rounded-xl px-4 py-2 text-sm font-semibold transition active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 sm:min-h-10";
  const styles =
    variant === "secondary"
      ? "border border-white/10 bg-dna-surface text-slate-100 hover:border-violet-500/30 hover:bg-dna-surfaceHover"
      : variant === "blue"
      ? "bg-blue-600 text-white shadow-md shadow-blue-900/25 hover:bg-blue-500"
      : variant === "danger"
      ? "bg-rose-600 text-white hover:bg-rose-500"
      : "bg-violet-600 text-white shadow-md shadow-violet-900/35 hover:bg-violet-500";
  return (
    <button
      type="button"
      title={title}
      className={[base, styles, className].filter(Boolean).join(" ")}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
