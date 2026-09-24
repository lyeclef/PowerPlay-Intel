import { QueryError } from "@/components/terminal/QueryError";
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Radio, ArrowUpRight, ArrowDownRight, Loader2, Zap } from "lucide-react";
import { fetchTape } from "@/lib/api";
import { fmtUsd, fmtCents, shortAddr, timeAgo, scoreColor } from "@/lib/format";
import { LabelSet } from "@/components/terminal/LabelSet";

const SIZE_FILTERS = [
  { label: "ALL", value: 0 },
  { label: "$1K+", value: 1000 },
  { label: "$10K+", value: 10000 },
  { label: "$50K+", value: 50000 },
];

export default function TapePage() {
  const [sharpOnly, setSharpOnly] = useState(false);
  const [minSize, setMinSize] = useState(0);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["tape", sharpOnly, minSize],
    queryFn: () => fetchTape(sharpOnly, minSize, 100),
    refetchInterval: 8000,
    refetchOnWindowFocus: true,
  });

  const trades = data?.trades || [];

  if (isError) return <QueryError onRetry={refetch} />;

  return (
    <div className="max-w-5xl mx-auto" data-testid="tape-page">
      <div className="mb-4">
        <div className="flex items-center gap-2 mb-1">
          <span className="h-1.5 w-1.5 rounded-full bg-sharp pulse-dot" />
          <span className="label-mono text-[10px] text-sharp">LIVE TRADE TAPE · POLYMARKET</span>
        </div>
        <h1 className="mono text-2xl sm:text-3xl font-black tracking-tight text-white uppercase">
          Smart Money <span className="text-brand">Hitting the Book</span>
        </h1>
        <p className="text-sm text-retail mt-1 max-w-2xl">
          Trades streaming in across sports &amp; esports, tagged with each wallet&apos;s archetype.
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        <button
          data-testid="tape-sharp-toggle"
          onClick={() => setSharpOnly((v) => !v)}
          className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1"
          style={
            sharpOnly
              ? { color: "#07090e", backgroundColor: "#00E599", borderColor: "#00E599" }
              : { color: "#00E599", borderColor: "#00E59955" }
          }
        >
          <Zap size={11} /> SHARP MONEY ONLY
        </button>
        <div className="h-5 w-px bg-hair mx-1" />
        <span className="label-mono text-[9px] text-retail px-1">MIN SIZE</span>
        {SIZE_FILTERS.map((f) => {
          const active = minSize === f.value;
          

  return (
            <button
              key={f.value}
              data-testid={`tape-size-${f.value}`}
              onClick={() => setMinSize(f.value)}
              className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors"
              style={
                active
                  ? { color: "#07090e", backgroundColor: "#A855F7", borderColor: "#A855F7" }
                  : { color: "#94A3B8", borderColor: "#1E2633" }
              }
            >
              {f.label}
            </button>
          );
        })}
        <span className="label-mono text-[9px] text-retail ml-auto flex items-center gap-1">
          <Radio size={11} className="text-sharp" /> {trades.length} PRINTS
        </span>
      </div>

      {/* Column header */}
      <div className="hidden sm:grid grid-cols-[64px_1fr_120px_110px] gap-3 px-3 py-2 label-mono text-[8px] text-retail border-b border-hair">
        <span>TIME</span>
        <span>WALLET · MARKET</span>
        <span className="text-right">ACTION</span>
        <span className="text-right">SIZE</span>
      </div>

      {isLoading && !trades.length ? (
        <div className="panel flex flex-col items-center justify-center gap-3 py-24 mt-2">
          <Loader2 className="animate-spin text-sharp" size={22} />
          <span className="label-mono text-[11px] text-retail">Reading the tape…</span>
        </div>
      ) : trades.length ? (
        <div data-testid="tape-list" className="divide-y divide-hair">
          <AnimatePresence initial={false}>
            {trades.map((t) => (
              <TapeRow key={t.id} t={t} />
            ))}
          </AnimatePresence>
        </div>
      ) : (
        <div className="panel p-16 text-center mt-2">
          <p className="label-mono text-xs text-retail">
            {sharpOnly ? "No sharp-money prints matching this filter yet." : "No recent trades."}
          </p>
        </div>
      )}
    </div>
  );
}

const TapeRow = ({ t }) => {
  const buy = t.side === "BUY";
  const actionColor = buy ? "#00E599" : "#FF3B5C";
  const Arrow = buy ? ArrowUpRight : ArrowDownRight;

  

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -8, backgroundColor: "rgba(0,229,153,0.08)" }}
      animate={{ opacity: 1, y: 0, backgroundColor: "rgba(0,0,0,0)" }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.5 }}
      data-testid="tape-row"
      className="grid grid-cols-[64px_1fr_120px_110px] gap-3 px-3 py-2.5 items-center relative"
      style={{
        borderLeft: t.sharp ? "2px solid #00E599" : "2px solid transparent",
        paddingLeft: t.sharp ? 10 : 12,
      }}
    >
      {/* time */}
      <span className="mono text-[10px] text-retail">{timeAgo(new Date(t.timestamp * 1000).toISOString())}</span>

      {/* wallet + market */}
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          {t.labels?.length ? (
            <LabelSet labels={t.labels} className="shrink-0" />
          ) : (
            <span className="label-mono text-[9px] text-retail px-1.5 py-0.5 rounded-sm border border-hair">
              UNCLASSIFIED
            </span>
          )}
          {t.smartScore != null && (
            <span className="mono text-[10px] font-bold" style={{ color: scoreColor(t.smartScore) }}>
              {Math.round(t.smartScore)}
            </span>
          )}
          <Link
            to={`/wallet/${t.address}`}
            target="_blank"
            rel="noopener noreferrer"
            className="mono text-[11px] text-slate-300 hover:text-brand transition-colors truncate"
            data-testid="tape-wallet-link"
          >
            {t.name || t.pseudonym || shortAddr(t.address)}
          </Link>
        </div>
        <div className="flex items-center gap-1.5 min-w-0">
          {t.icon && (
            <img src={t.icon} alt="" className="h-3.5 w-3.5 rounded-sm object-cover shrink-0" />
          )}
          <span className="text-[11px] text-retail truncate">{t.question}</span>
        </div>
      </div>

      {/* action */}
      <div className="text-right">
        <div className="flex items-center justify-end gap-1 mono text-[11px] font-bold" style={{ color: actionColor }}>
          <Arrow size={12} strokeWidth={2.5} />
          {t.side} {t.outcome}
        </div>
        <div className="mono text-[10px] text-slate-400">
          @ {fmtCents(t.price)}
        </div>
        {t.sharp && (
          <div className="flex items-center justify-end gap-1 mt-0.5">
            <span
              className="label-mono text-[8px] font-bold px-1.5 py-0.5 rounded-sm"
              style={{
                backgroundColor:
                  t.tailStatus === "BETTER_PRICE" ? "rgba(0,210,255,0.15)" :
                  t.tailStatus === "PRIME_TAIL" ? "rgba(0,229,153,0.15)" :
                  t.tailStatus === "ACCEPTABLE" ? "rgba(251,191,36,0.15)" :
                  "rgba(255,59,92,0.15)",
                color:
                  t.tailStatus === "BETTER_PRICE" ? "#00D2FF" :
                  t.tailStatus === "PRIME_TAIL" ? "#00E599" :
                  t.tailStatus === "ACCEPTABLE" ? "#FBBF24" :
                  "#FF3B5C",
                border: "1px solid currentColor"
              }}
            >
              {t.tailStatus === "BETTER_PRICE" ? "★ BETTER" :
               t.tailStatus === "PRIME_TAIL" ? "⚡ PRIME" :
               t.tailStatus === "ACCEPTABLE" ? "TAIL OK" :
               "MOVED"}
            </span>
            {t.currentPrice != null && (
              <span className="mono text-[8px] text-slate-500">
                now {Math.round(t.currentPrice * 100)}¢
              </span>
            )}
          </div>
        )}
      </div>

      {/* size */}
      <div className="text-right">
        <div className="mono text-sm font-black text-white">{fmtUsd(t.sizeUsd)}</div>
        <div className="mono text-[9px] text-retail">{Math.round(t.shares || 0).toLocaleString()} sh</div>
      </div>
    </motion.div>
  );
};
