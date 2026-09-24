import React from "react";

// Dual-sided smart-money strength: YES (green) vs NO (red), each 0-100.
export const SmartMeter = ({
  strengthYes = 0,
  strengthNo = 0,
  outcomes = ["Yes", "No"],
  netLean = 0,
  leanSide = "NEUTRAL",
  mode = "sharp",
  testId,
}) => {
  if (strengthYes == null || strengthNo == null || leanSide === "UNAVAILABLE") {
    return (
      <div data-testid={testId} className="text-xs text-slate-400 py-2">
        {mode === "combined"
          ? "No Smart Money signal yet (Sharps or Candidates)."
          : "No qualified Sharp signal yet."}
        <br />
        <span className="text-[10px]">Inspect wallet evidence and candidates below.</span>
      </div>
    );
  }
  const yesName = (outcomes?.[0] || "YES").toUpperCase();
  const noName = (outcomes?.[1] || "NO").toUpperCase();
  const label = mode === "combined" ? "NET SMART LEAN (SHARPS + CANDIDATES)" : "NET ALPHA LEAN (SHARPS)";
  return (
    <div data-testid={testId} className="space-y-1.5">
      <Bar name={yesName} score={strengthYes} color="#00E599" />
      <Bar name={noName} score={strengthNo} color="#FF3B5C" />
      <div className="flex items-center justify-between pt-0.5">
        <span className="label-mono text-[9px] text-retail">{label}</span>
        <span
          className="mono text-[10px] font-bold px-1.5 py-0.5 rounded-sm"
          style={{
            color: leanSide === "YES" ? "#00E599" : leanSide === "NO" ? "#FF3B5C" : "#94A3B8",
            backgroundColor:
              leanSide === "YES"
                ? "rgba(0,229,153,0.1)"
                : leanSide === "NO"
                ? "rgba(255,59,92,0.1)"
                : "rgba(148,163,184,0.08)",
          }}
        >
          {leanSide === "NEUTRAL"
            ? "SPLIT"
            : `${netLean > 0 ? "+" : ""}${netLean}% ${leanSide}`}
        </span>
      </div>
    </div>
  );
};

const Bar = ({ name, score, color }) => (
  <div className="flex items-center gap-2">
    <span className="mono text-[9px] w-14 shrink-0 truncate" style={{ color }}>
      {name}
    </span>
    <div className="flex-1 h-1.5 bg-[#141a23] rounded-sm overflow-hidden border border-hair">
      <div
        className="h-full rounded-sm transition-all duration-700"
        style={{
          width: `${Math.max(0, Math.min(100, score))}%`,
          backgroundColor: color,
          boxShadow: `0 0 8px ${color}88`,
        }}
      />
    </div>
    <span className="mono text-[10px] font-bold w-10 text-right" style={{ color }}>
      {Math.round(score)}%
    </span>
  </div>
);
