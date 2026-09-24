import React from "react";
import { ArrowUpRight, Flame, Zap, AlertTriangle, Target } from "lucide-react";
import { fmtUsd } from "@/lib/format";

export const SmartPickBanner = ({
  market,
  event,
  analysis,
  measureMode = "combined",
  className = "",
  compact = false,
}) => {
  const a = analysis || market?.analysis;
  const isCombined = measureMode === "combined";
  const activeAnalysis = isCombined && a?.combined ? a.combined : a;

  // Cross-cohort conflict check: did net smart lean switch sides between Sharps Only and Sharps + Candidates?
  const sharpLeanSide = a?.leanSide;
  const combLeanSide = a?.combined?.leanSide;
  const isSidesSwitched =
    sharpLeanSide &&
    combLeanSide &&
    ["YES", "NO"].includes(sharpLeanSide) &&
    ["YES", "NO"].includes(combLeanSide) &&
    sharpLeanSide !== combLeanSide;

  // Extract pick from activeAnalysis
  const rawPick = isCombined
    ? (activeAnalysis?.pick || a?.combined?.pick)
    : (activeAnalysis?.sharpPick || a?.sharpPick || activeAnalysis?.pick || a?.pick);

  const isConflict =
    isSidesSwitched ||
    rawPick?.conviction === "CONFLICT" ||
    rawPick?.isConflict;

  // Fallback to tailIntelligence only if not conflicted
  const pick =
    rawPick ||
    (!isConflict && a?.tailIntelligence?.pick) ||
    (!isConflict && a?.tailIntelligence?.sharpCapitalYes > a?.tailIntelligence?.sharpCapitalNo
      ? {
          side: "YES",
          outcome: market?.outcomes?.[0] || "Yes",
          conviction: "MODERATE",
          sharpCount: a?.tailIntelligence?.sharpCountYes || 0,
          candidateCount: a?.tailIntelligence?.candidateCountYes || 0,
          smartCount: (a?.tailIntelligence?.sharpCountYes || 0) + (a?.tailIntelligence?.candidateCountYes || 0),
          smartCapital: a?.tailIntelligence?.smartCapitalYes || a?.tailIntelligence?.sharpCapitalYes || 0,
          avgEntry: a?.tailIntelligence?.avgSmartEntryYes || a?.tailIntelligence?.avgSharpEntryYes,
          currentPrice: a?.tailIntelligence?.currentPriceYes || market?.prices?.[0],
          slippageCents: a?.tailIntelligence?.slippageYesCents,
        }
      : !isConflict && a?.tailIntelligence?.sharpCapitalNo > a?.tailIntelligence?.sharpCapitalYes
      ? {
          side: "NO",
          outcome: market?.outcomes?.[1] || "No",
          conviction: "MODERATE",
          sharpCount: a?.tailIntelligence?.sharpCountNo || 0,
          candidateCount: a?.tailIntelligence?.candidateCountNo || 0,
          smartCount: (a?.tailIntelligence?.sharpCountNo || 0) + (a?.tailIntelligence?.candidateCountNo || 0),
          smartCapital: a?.tailIntelligence?.smartCapitalNo || a?.tailIntelligence?.sharpCapitalNo || 0,
          avgEntry: a?.tailIntelligence?.avgSmartEntryNo || a?.tailIntelligence?.avgSharpEntryNo,
          currentPrice: a?.tailIntelligence?.currentPriceNo || market?.prices?.[1],
          slippageCents: a?.tailIntelligence?.slippageNoCents,
        }
      : null);

  const eventSlug = market?.eventSlug || event?.eventSlug || market?.slug || event?.slug || "";
  const polymarketUrl = eventSlug
    ? `https://polymarket.com/event/${eventSlug}`
    : "https://polymarket.com";

  const isYes = pick?.side === "YES";
  const pickColor = isYes ? "#00E599" : "#FF3B5C";
  const isHigh = pick?.conviction === "HIGH";
  const isCaution = pick?.conviction === "CAUTION";

  // Compact badge mode for cards
  if (compact) {
    if (isConflict) {
      return (
        <div className={`flex items-center gap-1.5 ${className}`}>
          <span
            className="inline-flex items-center gap-1 label-mono text-[9px] font-bold px-2 py-0.5 rounded-sm border border-amber-500/40 text-amber-400 bg-amber-500/10"
            title="Net smart lean switches sides between Sharps and Candidates — no solid pick"
          >
            <AlertTriangle size={10} />
            <span>SPLIT LEAN</span>
          </span>
        </div>
      );
    }
    if (!pick || !pick.outcome || !pick.side) return null;
    return (
      <div className={`flex items-center gap-1.5 ${className}`}>
        <span
          className="inline-flex items-center gap-1 label-mono text-[9px] font-bold px-2 py-0.5 rounded-sm border"
          style={{
            color: pickColor,
            backgroundColor: `${pickColor}1a`,
            borderColor: `${pickColor}55`,
          }}
          title={pick.verdict || `Smart Money on ${pick.outcome}`}
        >
          {isHigh ? <Flame size={10} className="text-amber-400" /> : <Zap size={10} />}
          <span>PICK: {pick.outcome}</span>
          {pick.currentPrice != null && (
            <span className="opacity-80">({Math.round(pick.currentPrice * 100)}¢)</span>
          )}
        </span>
      </div>
    );
  }

  // Full Hero Banner: Split Consensus State
  if (isConflict) {
    const sharpOutcome = sharpLeanSide === "YES" ? (market?.outcomes?.[0] || "Yes") : (market?.outcomes?.[1] || "No");
    const combOutcome = combLeanSide === "YES" ? (market?.outcomes?.[0] || "Yes") : (market?.outcomes?.[1] || "No");
    return (
      <div
        data-testid="smart-money-pick-card"
        className={`panel-2 p-4 rounded-sm border-l-4 relative overflow-hidden transition-all ${className}`}
        style={{
          borderLeftColor: "#FBBF24",
          background: `linear-gradient(135deg, rgba(22, 18, 12, 0.98) 0%, rgba(14, 12, 8, 0.98) 100%)`,
        }}
      >
        <div className="flex items-center justify-between gap-2 pb-2.5 mb-3 border-b border-hair">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center h-6 w-6 rounded-sm bg-amber-500/20 text-amber-400">
              <AlertTriangle size={14} />
            </div>
            <div>
              <div className="label-mono text-[9px] tracking-wider text-slate-400 uppercase">
                Consensus Intelligence
              </div>
              <div className="label-mono text-[11px] font-black text-amber-400 tracking-wide">
                SPLIT SMART MONEY CONSENSUS
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="inline-flex items-center gap-1 label-mono text-[9px] font-black px-2 py-0.5 rounded-sm bg-amber-500/20 border border-amber-500/50 text-amber-400">
              <AlertTriangle size={11} /> OPPOSING SIGNALS
            </span>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-baseline gap-2.5 flex-wrap">
              <span className="mono text-lg sm:text-xl font-black uppercase tracking-tight text-amber-300">
                Net Smart Lean Switches Sides
              </span>
            </div>
            <p className="text-xs text-slate-300 mt-1.5 leading-relaxed">
              Sharps favor <b className="text-white">{sharpOutcome}</b> ({a?.netLean ? `${a.netLean > 0 ? "+" : ""}${a.netLean}%` : sharpLeanSide}), but Candidates pull the market to <b className="text-white">{combOutcome}</b> ({a?.combined?.netLean ? `${a.combined.netLean > 0 ? "+" : ""}${a.combined.netLean}%` : combLeanSide}).
            </p>
            <p className="mono text-[11px] text-slate-400 mt-1">
              Recommendation: <b>Exercise caution</b> — verified Sharps and Candidates take opposing sides. No solid pick advised.
            </p>
          </div>

          <div className="shrink-0 self-start sm:self-center">
            <a
              href={polymarketUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm border border-hair bg-surface hover:bg-[#1a2230] text-slate-300 hover:text-white label-mono font-bold text-xs tracking-wider uppercase transition-colors"
            >
              <span>View on Polymarket</span>
              <ArrowUpRight size={14} />
            </a>
          </div>
        </div>
      </div>
    );
  }

  if (!pick || !pick.outcome || !pick.side) {
    return (
      <div
        className={`panel p-3.5 rounded-sm border border-hair bg-[#0d131a] flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${className}`}
      >
        <div className="flex items-center gap-2 text-slate-400 text-xs">
          <span className="h-2 w-2 rounded-full bg-slate-500" />
          <span>
            <b>No Smart Money Consensus Yet</b> · Order book currently led by casuals or market makers.
          </span>
        </div>
        <a
          href={polymarketUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 label-mono text-[10px] font-bold px-3 py-1.5 rounded-sm border border-hair bg-surface hover:bg-[#1a2230] text-slate-300 hover:text-white transition-colors shrink-0"
        >
          <span>View on Polymarket</span>
          <ArrowUpRight size={12} />
        </a>
      </div>
    );
  }

  return (
    <div
      data-testid="smart-money-pick-card"
      className={`panel-2 p-4 rounded-sm border-l-4 relative overflow-hidden transition-all ${className}`}
      style={{
        borderLeftColor: pickColor,
        background: `linear-gradient(135deg, rgba(13, 20, 30, 0.98) 0%, rgba(10, 15, 22, 0.98) 100%)`,
      }}
    >
      {/* Top Banner Row */}
      <div className="flex items-center justify-between gap-2 pb-2.5 mb-3 border-b border-hair">
        <div className="flex items-center gap-2">
          <div
            className="flex items-center justify-center h-6 w-6 rounded-sm"
            style={{ backgroundColor: `${pickColor}22` }}
          >
            <Target size={14} style={{ color: pickColor }} />
          </div>
          <div>
            <div className="label-mono text-[9px] tracking-wider text-slate-400 uppercase">
              Consensus Intelligence
            </div>
            <div className="label-mono text-[11px] font-black text-white tracking-wide">
              SMART MONEY PICK
            </div>
          </div>
        </div>

        {/* Conviction Tag */}
        <div className="flex items-center gap-1.5">
          {isHigh ? (
            <span className="inline-flex items-center gap-1 label-mono text-[9px] font-black px-2 py-0.5 rounded-sm bg-[#00E59920] border border-[#00E59966] text-[#00E599]">
              <Flame size={11} className="text-[#00E599]" /> HIGH CONVICTION
            </span>
          ) : isCaution ? (
            <span className="inline-flex items-center gap-1 label-mono text-[9px] font-black px-2 py-0.5 rounded-sm bg-[#FBBF2420] border border-[#FBBF2466] text-[#FBBF24]">
              <AlertTriangle size={11} className="text-[#FBBF24]" /> LINE MOVED
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 label-mono text-[9px] font-black px-2 py-0.5 rounded-sm bg-[#00D2FF20] border border-[#00D2FF66] text-[#00D2FF]">
              <Zap size={11} className="text-[#00D2FF]" /> SOLID SIGNAL
            </span>
          )}
        </div>
      </div>

      {/* Main Pick Highlight & Action */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-baseline gap-2.5 flex-wrap">
            <span
              className="mono text-xl sm:text-2xl font-black uppercase tracking-tight"
              style={{ color: pickColor }}
            >
              {pick.outcome}
            </span>
            <span className="label-mono text-xs font-bold text-slate-300 px-1.5 py-0.5 rounded-sm bg-surface2 border border-hair">
              {pick.side}
            </span>
            {pick.currentPrice != null && (
              <span className="mono text-base font-bold text-white">
                Live: {Math.round(pick.currentPrice * 100)}¢
              </span>
            )}
          </div>

          {/* Pricing & Edge Context */}
          <div className="mono text-xs text-slate-300 mt-2 space-y-1">
            {pick.avgEntry != null && (
              <p>
                Smart Money Avg Entry:{" "}
                <span className="font-bold text-white">{Math.round(pick.avgEntry * 100)}¢</span>
                {pick.slippageCents != null && (
                  <span
                    className="ml-1.5 font-semibold"
                    style={{
                      color:
                        pick.slippageCents <= 0
                          ? "#00D2FF"
                          : pick.slippageCents <= 3
                          ? "#00E599"
                          : "#FBBF24",
                    }}
                  >
                    ({pick.slippageCents <= 0 ? "★ Better than smart entry" : `+${pick.slippageCents}¢ slippage`})
                  </span>
                )}
              </p>
            )}
            <p className="text-[11px] text-slate-400">
              Backed by{" "}
              <b className="text-slate-200">
                {pick.sharpCount || 0} Sharp{pick.sharpCount === 1 ? "" : "s"}
              </b>{" "}
              and{" "}
              <b className="text-slate-200">
                {pick.candidateCount || 0} Candidate{pick.candidateCount === 1 ? "" : "s"}
              </b>{" "}
              ({fmtUsd(pick.smartCapital || 0)} smart money)
            </p>
          </div>
        </div>

        {/* 1-Click Bet on Polymarket Button */}
        <div className="shrink-0 self-start sm:self-center">
          <a
            href={polymarketUrl}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="bet-on-polymarket-btn"
            className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm label-mono font-black text-xs tracking-wider transition-all duration-200 shadow-md group cursor-pointer"
            style={{
              backgroundColor: pickColor,
              color: "#07090e",
              boxShadow: `0 0 16px ${pickColor}33`,
            }}
          >
            <span>Bet on Polymarket</span>
            <ArrowUpRight
              size={15}
              strokeWidth={2.5}
              className="group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform"
            />
          </a>
        </div>
      </div>
    </div>
  );
};
