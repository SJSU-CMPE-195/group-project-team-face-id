import React from "react";
import Card from "./Card";
import { ScanFace } from "lucide-react";

export default function ControlTab() {
  return (
    <div className="flex justify-center pt-1">
      <div className="w-full max-w-4xl xl:max-w-5xl">
        <Card>
          <div className="flex flex-col items-center text-center sm:flex-row sm:items-start sm:text-left">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-violet-500/25 bg-violet-500/10 sm:mr-4">
              <ScanFace className="h-6 w-6 text-violet-400" strokeWidth={1.75} />
            </div>
            <div className="mt-4 sm:mt-0">
              <div className="text-lg font-semibold text-slate-100">Face scan</div>
              <div className="mt-1 text-sm text-slate-400">
                Live preview and match result from the Pi will appear here.
              </div>
            </div>
          </div>
          <div className="mt-6 flex min-h-[min(52vh,520px)] w-full items-center justify-center rounded-2xl border border-dashed border-white/10 bg-dna-bg text-slate-500">
            <span className="text-sm">Camera preview area</span>
          </div>
        </Card>
      </div>
    </div>
  );
}
