import React from "react";

export default function Badge({ children }) {
  return (
    <span className="inline-flex items-center rounded-full border border-neutral-200 bg-white px-2.5 py-1 text-xs text-neutral-700">
      {children}
    </span>
  );
}
