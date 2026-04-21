import React from "react";

export default function Badge({ children }) {
  return (
    <span className="inline-flex items-center rounded-full border border-violet-500/30 bg-violet-500/10 px-2.5 py-1 text-xs font-medium text-violet-200/95">
      {children}
    </span>
  );
}
