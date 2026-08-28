import React from "react";

export default function Card({ children, contentClassName = "p-4 sm:p-5" }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-dna-surface shadow-panel shadow-panel-inset backdrop-blur-sm">
      <div className={contentClassName}>{children}</div>
    </div>
  );
}
