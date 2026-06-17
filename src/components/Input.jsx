import React from "react";

export default function Input({ value, onChange, placeholder, type = "text", disabled, className = "", ...props }) {
  return (
    <input
      {...props}
      type={type}
      value={value}
      disabled={disabled}
      onChange={onChange}
      placeholder={placeholder}
      className={[
        "w-full rounded-xl border border-white/10 bg-dna-bg px-3 py-2 text-sm text-slate-100 outline-none ring-violet-500/0 transition placeholder:text-slate-500 focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/25 disabled:opacity-50",
        className,
      ].filter(Boolean).join(" ")}
    />
  );
}
