import React from "react";

export default function Card({ children }) {
  return (
    <div className="rounded-3xl bg-white shadow-sm ring-1 ring-neutral-200">
      <div className="p-5">{children}</div>
    </div>
  );
}
