import React from "react";

export default function Card({ children }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-dna-surface shadow-panel shadow-panel-inset backdrop-blur-sm">
      <div className="p-5">{children}</div>
    </div>
  );
}
