import React from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchStats } from "@/lib/api";
import { fmtUsd, fmtNum } from "@/lib/format";

export const TickerBar = () => {
  const { data } = useQuery({
    queryKey: ["stats"],
    queryFn: fetchStats,
    refetchInterval: 30000,
  });

  const items = [
    { label: "TRACKED WALLETS", value: fmtNum(data?.trackedWallets || 0), color: "#f1f5f9", testId: "ticker-tracked-wallets" },
    { label: "SHARP WALLETS", value: fmtNum(data?.sharpWallets || 0), color: "#00E599" },
    { label: "SMART CAPITAL", value: fmtUsd(data?.smartCapital || 0), color: "#00D2FF", testId: "ticker-total-volume" },
    { label: "MARKETS ANALYZED", value: fmtNum(data?.analyzedMarkets || 0), color: "#f1f5f9" },
    { label: "YES-LEAN BIAS", value: data?.yesLeanPct == null ? "—" : `${data.yesLeanPct}%`, color: "#A855F7", testId: "ticker-esports-dominance" },
    { label: "SHARP AVG WINRATE", value: data?.sharpAvgWinrate == null ? "—" : `${data.sharpAvgWinrate}%`, color: "#00E599", testId: "ticker-sharp-accuracy" },
  ];

  const row = (keyPrefix) => (
    <div className="flex items-center shrink-0">
      {items.map((it, i) => (
        <div key={`${keyPrefix}-${i}`} data-testid={it.testId} className="flex items-center gap-2 px-6 border-r border-hair">
          <span className="label-mono text-[9px] text-retail">{it.label}</span>
          <span className="mono text-[11px] font-bold" style={{ color: it.color }}>
            {it.value}
          </span>
        </div>
      ))}
    </div>
  );

  return (
    <div className="border-b border-hair bg-surface/60 overflow-hidden relative">
      <div className="flex items-center">
        <div className="flex items-center gap-1.5 px-3 py-2 border-r border-hair bg-canvas shrink-0 z-10">
          <span className="h-1.5 w-1.5 rounded-full bg-brand pulse-dot" />
          <span className="label-mono text-[9px] text-brand">LIVE</span>
        </div>
        <div className="flex marquee">
          {row("a")}
          {row("b")}
        </div>
      </div>
    </div>
  );
};
