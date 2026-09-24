import React from "react";
import { motion } from "framer-motion";
import { Users, Crosshair, Clock, Zap, ArrowUpRight } from "lucide-react";
import { SmartPickBanner } from "./SmartPickBanner";
import { SmartMeter } from "./SmartMeter";
import { ProfilingStatus } from "./ProfilingStatus";
import { fmtUsd, fmtCents, countdown } from "@/lib/format";
import { pickDisplayMarket, MARKET_TYPE_META } from "@/lib/api";

const TYPE_ORDER = ["moneyline", "spread", "total", "prop"];

export const EventCard = ({ event, marketType, measureMode = "sharp", index = 0, onOpen }) => {
  const display = pickDisplayMarket(event, marketType);
  if (!display) return null;
  const a = display.analysis;
  const prices = display.prices || [0.5, 0.5];
  const cd = countdown(display.gameStartTime || event.gameStartTime || event.endDate);

  const isCombined = measureMode === "combined";
  const activeMetrics = isCombined && a?.combined ? a.combined : a;
  const alphaSignal = isCombined ? a?.combined?.alpha : a?.alpha;
  const eventSlug = display.eventSlug || event.eventSlug || display.slug || event.slug || "";
  const polymarketUrl = eventSlug ? `https://polymarket.com/event/${eventSlug}` : "https://polymarket.com";

  return (
    <motion.div
      data-testid="market-row-item"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: Math.min(index * 0.03, 0.4) }}
      className="panel w-full p-3 hover:border-[#2a3649] hover:bg-[#0f141d] transition-colors duration-200"
    >
      <button className="text-left w-full" onClick={() => onOpen(event, display.id)}>
        <div className="flex items-start gap-2.5">
          {event.icon ? (
            <img
              src={event.icon}
              alt=""
              className="h-9 w-9 rounded-sm object-cover border border-hair shrink-0 mt-0.5"
              onError={(e) => (e.target.style.display = "none")}
            />
          ) : null}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 mb-0.5">
              <span className="label-mono text-[9px] text-brand truncate">{event.category}</span>
              {cd && (
                <span className="label-mono text-[9px] text-retail flex items-center gap-0.5">
                  <Clock size={9} /> {cd}
                </span>
              )}
              <span className="ml-auto flex items-center gap-1.5">
                <SmartPickBanner market={display} event={event} measureMode={measureMode} compact={true} />
                {alphaSignal?.divergent && (
                  <span className="label-mono text-[8px] text-[#FFB020] border border-[#FFB02055] rounded-sm px-1 flex items-center gap-0.5">
                    <Zap size={8} /> ALPHA
                  </span>
                )}
                <span className="label-mono text-[8px] text-insider border border-[#00D2FF44] rounded-sm px-1">
                  {MARKET_TYPE_META[display.marketType]?.label || display.marketType}
                </span>
                <a
                  href={polymarketUrl}
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
            <h3 className="text-sm font-semibold text-slate-100 leading-snug line-clamp-2">
              {display.question}
            </h3>
          </div>
        </div>

        <div className="flex items-center gap-2 mt-2.5 mb-3">
          <PricePill testId="market-yes-price" label={display.outcomes?.[0] || "Yes"} price={prices[0]} color="#00E599" />
          <PricePill testId="market-no-price" label={display.outcomes?.[1] || "No"} price={prices[1]} color="#FF3B5C" />
          <span className="mono text-[10px] text-retail ml-auto">VOL {fmtUsd(display.volume)}</span>
        </div>

        <ProfilingStatus status={display.profiling} analysis={a} />
        {a && (a.participantCount > 0 || !a.pendingWallets) ? (
          <>
            <SmartMeter
              testId="smart-money-strength-gauge"
              strengthYes={activeMetrics?.strengthYes}
              strengthNo={activeMetrics?.strengthNo}
              outcomes={display.outcomes}
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
      </button>

      {/* submarket type chips */}
      <div className="flex flex-wrap items-center gap-1 mt-2.5 pt-2.5 border-t border-hair">
        {TYPE_ORDER.filter((t) => event.typeCounts?.[t]).map((t) => {
          const first = (event.markets || []).find((m) => m.marketType === t);
          const active = display.marketType === t;
          return (
            <button
              key={t}
              data-testid={`event-type-chip-${t}`}
              onClick={() => first && onOpen(event, first.id)}
              className="label-mono text-[9px] px-1.5 py-0.5 rounded-sm border transition-colors"
              style={
                active
                  ? { color: "#00D2FF", borderColor: "#00D2FF66", backgroundColor: "#00D2FF14" }
                  : { color: "#94A3B8", borderColor: "#1E2633" }
              }
            >
              {MARKET_TYPE_META[t]?.label || t} · {event.typeCounts[t]}
            </button>
          );
        })}
      </div>
    </motion.div>
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
