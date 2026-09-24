import React from "react";
import { motion } from "framer-motion";
import { Users, Crosshair, Clock, ArrowUpRight } from "lucide-react";
import { SmartPickBanner } from "./SmartPickBanner";
import { SmartMeter } from "./SmartMeter";
import { ProfilingStatus } from "./ProfilingStatus";
import { fmtUsd, fmtCents, countdown } from "@/lib/format";

export const MarketCard = ({ market, measureMode = "sharp", index = 0, onSelect }) => {
  const a = market.analysis;
  const prices = market.prices || [0.5, 0.5];
  const cd = countdown(market.endDate);
  const isCombined = measureMode === "combined";
  const activeMetrics = isCombined && a?.combined ? a.combined : a;

  return (
    <motion.button
      data-testid="market-row-item"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: Math.min(index * 0.03, 0.4) }}
      onClick={() => onSelect(market)}
      className="panel text-left w-full p-3 group hover:border-[#2a3649] hover:bg-[#0f141d] transition-colors duration-200"
    >
      <div className="flex items-start gap-2.5">
        {market.icon ? (
          <img
            src={market.icon}
            alt=""
            className="h-9 w-9 rounded-sm object-cover border border-hair shrink-0 mt-0.5"
            onError={(e) => (e.target.style.display = "none")}
          />
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="label-mono text-[9px] text-sharp truncate">
              {market.category}
            </span>
            {cd && (
              <span className="label-mono text-[9px] text-retail flex items-center gap-0.5">
                <Clock size={9} /> {cd}
              </span>
            )}
            <span className="ml-auto flex items-center gap-1.5">
              <SmartPickBanner market={market} measureMode={measureMode} compact={true} />
              <a
                href={`https://polymarket.com/event/${market.eventSlug || market.slug || ""}`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="p-0.5 text-slate-500 hover:text-white transition-colors"
                title="Open on Polymarket"
              >
                <ArrowUpRight size={13} />
              </a>
            </span>
          </div>
          <h3 className="text-sm font-semibold text-slate-100 leading-snug line-clamp-2 group-hover:text-white">
            {market.question}
          </h3>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-2.5 mb-3">
        <PricePill testId="market-yes-price" label={(market.outcomes?.[0] || "Yes")} price={prices[0]} color="#00E599" />
        <PricePill testId="market-no-price" label={(market.outcomes?.[1] || "No")} price={prices[1]} color="#FF3B5C" />
        <span className="mono text-[10px] text-retail ml-auto">
          VOL {fmtUsd(market.volume)}
        </span>
      </div>

      <ProfilingStatus status={market.profiling} analysis={a} />
      {a && (a.participantCount > 0 || !a.pendingWallets) ? (
        <>
          <SmartMeter
            testId="smart-money-strength-gauge"
            strengthYes={activeMetrics?.strengthYes}
            strengthNo={activeMetrics?.strengthNo}
            outcomes={market.outcomes}
            netLean={activeMetrics?.netLean}
            leanSide={activeMetrics?.leanSide}
            mode={measureMode}
          />
          <div className="flex items-center gap-3 mt-2.5 pt-2.5 border-t border-hair">
            <span data-testid="market-wallet-count" className="mono text-[10px] text-retail flex items-center gap-1">
              <Users size={11} /> {a.participantCount}
            </span>
            {isCombined ? (
              <span className="mono text-[10px] flex items-center gap-1 text-slate-200">
                <Crosshair size={11} className="text-sharp" /> {a.sharpCount || 0} SHARP
                {(a.candidateCount > 0) && (
                  <span className="text-[#A855F7] font-semibold ml-1">
                    · {a.candidateCount} CANDIDATE{a.candidateCount > 1 ? "S" : ""}
                  </span>
                )}
              </span>
            ) : (
              <span className="mono text-[10px] flex items-center gap-1 text-sharp">
                <Crosshair size={11} /> {a.sharpCount || 0} SHARP
              </span>
            )}
            {a.holdToResolution && (
              <span className="mono text-[10px] text-retail ml-auto">
                {a.holdToResolution.ratio == null ? "—" : `${Math.round(a.holdToResolution.ratio * 100)}%`} RETAINED
              </span>
            )}
          </div>
        </>
      ) : null}
    </motion.button>
  );
};

const PricePill = ({ label, price, color, testId }) => (
  <span
    data-testid={testId}
    className="mono text-[10px] font-semibold px-1.5 py-0.5 rounded-sm border flex items-center gap-1"
    style={{ color, borderColor: `${color}44`, backgroundColor: `${color}12` }}
  >
    <span className="truncate max-w-[70px]">{label}</span>
    <span className="font-bold">{fmtCents(price)}</span>
  </span>
);
