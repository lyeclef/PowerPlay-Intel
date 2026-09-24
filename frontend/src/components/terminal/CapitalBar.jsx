import React from "react";
import { walletMeta } from "@/lib/meta";

export const CapitalBar = ({ distribution = [], testId }) => {
  const total = distribution.reduce((a, d) => a + (d.pct || 0), 0) || 1;
  return (
    <div data-testid={testId} className="space-y-2">
      <div className="flex h-3 w-full rounded-sm overflow-hidden border border-hair">
        {distribution.map((d) => {
          const m = walletMeta(d.category);
          return (
            <div
              key={d.category}
              className="h-full transition-all duration-500"
              style={{
                width: `${(d.pct / total) * 100}%`,
                backgroundColor: m.color,
              }}
              title={`${m.label} ${d.pct}%`}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {distribution.map((d) => {
          const m = walletMeta(d.category);
          return (
            <div key={d.category} className="flex items-center gap-1.5">
              <span
                className="h-2 w-2 rounded-sm"
                style={{ backgroundColor: m.color }}
              />
              <span className="mono text-[10px] text-slate-300">
                {m.label} <span className="text-retail">{d.pct}%</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
