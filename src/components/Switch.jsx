import React from "react";

export default function Switch({ checked, onChange, ariaLabel, disabled = false }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      disabled={disabled}
      className="flex h-12 w-14 shrink-0 items-center justify-center rounded-xl disabled:cursor-not-allowed disabled:opacity-50"
      aria-pressed={checked}
      aria-label={ariaLabel}
    >
      <span className={`block h-7 w-12 rounded-full p-1 transition ${checked ? "bg-fuchsia-600 shadow-[0_0_14px_-2px_rgba(217,70,239,0.55)]" : "bg-slate-700"}`}>
        <span className={`block h-5 w-5 rounded-full bg-dna-bg shadow-sm transition ${checked ? "translate-x-5" : "translate-x-0"}`} />
      </span>
    </button>
  );
}
