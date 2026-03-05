import React from "react";

export default function Switch({ checked, onChange }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`h-7 w-12 rounded-full p-1 transition ${
        checked ? "bg-neutral-900" : "bg-neutral-200"
      }`}
      aria-pressed={checked}
    >
      <div
        className={`h-5 w-5 rounded-full bg-white transition ${
          checked ? "translate-x-5" : "translate-x-0"
        }`}
      />
    </button>
  );
}
