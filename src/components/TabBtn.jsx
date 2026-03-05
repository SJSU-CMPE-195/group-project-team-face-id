import React from "react";

export default function TabBtn({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-2xl px-3 py-2 text-sm font-medium ring-1 transition ${
        active
          ? "bg-neutral-900 text-white ring-neutral-900"
          : "bg-white text-neutral-800 ring-neutral-200 hover:bg-neutral-50"
      }`}
    >
      {children}
    </button>
  );
}
