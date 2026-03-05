import React from "react";

export default function Btn({ variant = "primary", disabled, onClick, children }) {
  const base =
    "inline-flex items-center justify-center rounded-2xl px-4 py-2 text-sm font-medium transition active:scale-[0.99] disabled:opacity-50 disabled:cursor-not-allowed";
  const styles =
    variant === "secondary"
      ? "bg-neutral-100 text-neutral-900 hover:bg-neutral-200"
      : variant === "danger"
      ? "bg-red-600 text-white hover:bg-red-700"
      : "bg-neutral-900 text-white hover:bg-neutral-800";
  return (
    <button className={`${base} ${styles}`} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}
