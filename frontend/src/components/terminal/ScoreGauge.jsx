import React from "react";
import { scoreColor } from "@/lib/format";

// Radial 0-100 gauge for the smart-money composite score.
export const ScoreGauge = ({ score = null, size = 132, label = "SHARP RANK", testId }) => {
  const measured = typeof score === "number" && Number.isFinite(score);
  const v = Math.max(0, Math.min(100, Number(score) || 0));
  const color = measured ? scoreColor(v) : "#64748B";
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const dash = (v / 100) * c * 0.75; // 270deg arc
  const gap = c - dash;
  return (
    <div data-testid={testId} className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-[135deg]">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#1e2633"
          strokeWidth={stroke}
          strokeDasharray={`${c * 0.75} ${c * 0.25}`}
          strokeLinecap="round"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeDasharray={`${dash} ${gap}`}
          strokeLinecap="round"
          style={{ transition: "stroke-dasharray 0.8s ease", filter: `drop-shadow(0 0 6px ${color}88)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="mono text-3xl font-black leading-none" style={{ color }}>
          {measured ? v.toFixed(0) : "—"}
        </span>
        <span className="label-mono text-[8px] text-retail mt-1">{label}</span>
      </div>
    </div>
  );
};

export const ScoreChip = ({ score, testId }) => {
  const measured = typeof score === "number" && Number.isFinite(score);
  const v = measured ? score : 0;
  const color = measured ? scoreColor(v) : "#64748B";
  return (
    <span
      data-testid={testId}
      title={measured ? "Ranking among qualified Sharps; not a win probability" : "No ranking: Sharp qualification not established in this scope"}
      className="mono text-xs font-bold px-1.5 py-0.5 rounded-sm border"
      style={{ color, borderColor: `${color}55`, backgroundColor: `${color}14` }}
    >
      {measured ? v.toFixed(0) : "—"}
    </span>
  );
};
