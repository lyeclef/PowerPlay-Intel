import { QueryError } from "@/components/terminal/QueryError";
import { ProfilingStatus, profilingActive } from "./ProfilingStatus";
import React, { useState, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Loader2, TrendingUp, TrendingDown, Minus, ArrowUpRight, Info, Zap, Sparkles, Target, ShieldCheck, AlertTriangle, Crosshair, Users } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { fetchMarketDetail, MARKET_TYPE_META } from "@/lib/api";
import { fmtUsd, fmtCents, fmtPct } from "@/lib/format";
import { SmartMeter } from "./SmartMeter";
import { CapitalBar } from "./CapitalBar";
import { LabelSet } from "./LabelSet";
import { ScoreChip } from "./ScoreGauge";
import { SmartPickBanner } from "./SmartPickBanner";
import { shortAddr } from "@/lib/format";

const TYPE_ORDER = ["moneyline", "spread", "total", "prop"];

export const MarketDetail = ({ event, initialId, open, onOpenChange, initialMode = "sharp" }) => {
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState(initialId);
  const [measureMode, setMeasureMode] = useState(initialMode);

  useEffect(() => {
    setSelectedId(initialId);
  }, [initialId, event?.eventSlug]);

  useEffect(() => {
    if (initialMode) setMeasureMode(initialMode);
  }, [initialMode, open]);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["market", selectedId],
    queryFn: () => fetchMarketDetail(selectedId),
    enabled: !!selectedId && open,
    refetchOnWindowFocus: false,
    refetchInterval: query => open && profilingActive(query.state.data?.profiling) ? 1500 : false,
  });

  const a = data?.analysis;
  const m = data?.market;
  const isCombined = measureMode === "combined";
  const activeMetrics = isCombined && a?.combined ? a.combined : a;
  const retryProfiling = async () => {
    try { await fetchMarketDetail(selectedId, true); } catch (_) { /* Refetch exposes connection errors below. */ }
    await refetch();
  };

  const [showAllMarkets, setShowAllMarkets] = useState(false);

  const formatSubmarketLabel = (q, type) => {
    if (!q) return "";
    if (type === "spread") {
      return q.replace(/^spread:\s*/i, "").trim();
    }
    if (type === "total") {
      const match = q.match(/(?:o\/u|over\/under|total)\s*([0-9]+(?:\.[0-9]+)?)/i);
      if (match) return `O/U ${match[1]}`;
      return q.replace(/^.*:\s*/, "");
    }
    if (type === "moneyline") {
      if (/1h/i.test(q)) return "1H Moneyline";
      if (/1q/i.test(q)) return "1Q Moneyline";
      return "Game Moneyline";
    }
    return q.length > 28 ? q.slice(0, 26) + "…" : q;
  };

  const allMarkets = useMemo(() => {
    const list = [...(event?.markets || [])];
    if (m && m.id && !list.some((x) => x.id === m.id)) {
      list.push(m);
    }
    return list;
  }, [event, m]);

  const groups = useMemo(() => {
    const g = { moneyline: [], spread: [], total: [], prop: [] };
    allMarkets.forEach((mk) => (g[mk.marketType] || g.prop).push(mk));
    return g;
  }, [allMarkets]);

  const totalSubmarketsCount = allMarkets.length;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        data-testid="market-detail-container"
        side="right"
        className="w-full sm:max-w-2xl bg-canvas border-l border-hair p-0 overflow-y-auto"
      >
        {/* Sticky top bar: compact and non-blocking on mobile */}
        <SheetHeader className="p-4 sm:p-5 border-b border-hair sticky top-0 bg-canvas/95 backdrop-blur z-10">
          <div className="flex items-center gap-1.5 mb-1">
            <span className="label-mono text-[9px] text-brand uppercase font-bold">{event?.category}</span>
            <span className="label-mono text-[9px] text-retail truncate">· {event?.title}</span>
            <a
              href={`https://polymarket.com/event/${m?.eventSlug || event?.eventSlug || m?.slug || ""}`}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="header-polymarket-link"
              className="ml-auto inline-flex items-center gap-1 label-mono text-[10px] text-brand hover:text-white transition-colors"
              title="Open event on Polymarket"
            >
              <span>Polymarket</span>
              <ArrowUpRight size={12} />
            </a>
          </div>
          <SheetTitle className="text-left mono text-base sm:text-lg font-black text-white leading-snug pr-6 tracking-tight">
            {m?.question || event?.title}
          </SheetTitle>
          <SheetDescription className="sr-only">
            Smart-money analysis and wallet breakdown for {event?.title}
          </SheetDescription>
        </SheetHeader>

        {/* Submarket Selector: in regular scroll flow so it never blocks mobile scrolling */}
        {totalSubmarketsCount > 1 && (
          <div className="p-4 pb-2 border-b border-hair bg-[#0b1017]/60 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="label-mono text-[9px] text-retail font-bold tracking-wider uppercase">
                Submarkets ({totalSubmarketsCount})
              </span>
              {totalSubmarketsCount > 4 && (
                <button
                  type="button"
                  onClick={() => setShowAllMarkets((prev) => !prev)}
                  className="label-mono text-[9px] text-indigo-400 hover:text-indigo-300 underline underline-offset-2 transition-colors cursor-pointer"
                >
                  {showAllMarkets ? "Show main & alternatives" : "Show all lines"}
                </button>
              )}
            </div>

            {TYPE_ORDER.filter((t) => groups[t]?.length).map((t) => {
              const items = groups[t];
              const visibleItems = showAllMarkets
                ? items
                : items.length > 3
                ? [
                    ...items.slice(0, 3),
                    ...(items.slice(3).some((x) => x.id === selectedId)
                      ? items.filter((x) => x.id === selectedId)
                      : []),
                  ]
                : items;

              return (
                <div key={t} className="space-y-1">
                  <div className="label-mono text-[8px] text-slate-400 uppercase tracking-wider font-semibold">
                    {MARKET_TYPE_META[t]?.label || t}
                  </div>
                  <div className="flex flex-wrap gap-1.5 items-center">
                    {visibleItems.map((sm) => {
                      const active = sm.id === selectedId;
                      const displayLabel = formatSubmarketLabel(sm.question, sm.marketType);
                      return (
                        <button
                          key={sm.id}
                          data-testid="submarket-chip"
                          onClick={() => setSelectedId(sm.id)}
                          className="label-mono text-[9px] px-2 py-1 rounded-sm border text-left max-w-[260px] truncate transition-all duration-150 cursor-pointer"
                          style={
                            active
                              ? {
                                  color: "#07090e",
                                  backgroundColor: "#A855F7",
                                  borderColor: "#A855F7",
                                  fontWeight: 700,
                                }
                              : {
                                  color: "#CBD5E1",
                                  backgroundColor: "#131b26",
                                  borderColor: "#1E2633",
                                }
                          }
                          title={sm.question}
                        >
                          {displayLabel}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="px-5"><ProfilingStatus status={isLoading ? {state:"profiling"} : data?.profiling} analysis={a} onRetry={retryProfiling} /></div>
        {isError && a && <p role="status" className="px-5 text-xs text-amber-300">Refresh unavailable. Saved results remain visible. <button className="underline" onClick={() => refetch()}>Retry connection</button></p>}
        {isError && !a ? <QueryError onRetry={refetch} /> : isLoading || !a || (!a.participantCount && a.pendingWallets) ? (
          <div className="flex flex-col items-center justify-center gap-3 py-24 text-retail">
            {(isLoading || profilingActive(data?.profiling)) && <Loader2 className="animate-spin text-insider" size={26} />}
            <span className="label-mono text-[11px]">{data?.profiling?.state === "error" ? "Unable to finish profiling" : "Preparing wallet results…"}</span>
            <span className="mono text-[10px] text-slate-600">Completed wallets appear here as they finish</span>
          </div>
        ) : (
          <div className="p-5 space-y-5">
            {/* Smart Money Pick Banner — Incorporates Sharps & Candidates */}
            <SmartPickBanner
              market={m}
              event={event}
              analysis={a}
              measureMode={measureMode}
            />

            {/* AI intel — structured */}
            <div
              data-testid="detail-ai-narrative-summary"
              className="panel-2 p-4 border-l-2"
              style={{ borderLeftColor: "#00D2FF" }}
            >
              <div className="label-mono text-[9px] text-insider mb-2 flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-insider pulse-dot" /> EVIDENCE SUMMARY
              </div>
              <p className="text-sm font-semibold text-white leading-relaxed mb-3">
                {a.intel?.verdict}
              </p>
              <ul className="space-y-2">
                {(a.intel?.bullets || []).map((b, i) => (
                  <li key={i} className="text-xs text-slate-300 leading-relaxed flex items-start gap-2">
                    <span className="mt-1.5 h-1 w-1 rounded-full bg-insider shrink-0" />
                    {b}
                  </li>
                ))}
              </ul>
            </div>

            {activeMetrics?.alpha?.divergent && (
              <div
                data-testid="detail-alpha-signal"
                className="panel-2 p-3 border-l-2 flex items-start gap-2.5"
                style={{ borderLeftColor: "#FFB020" }}
              >
                <Zap size={16} className="text-[#FFB020] mt-0.5 shrink-0" />
                <div>
                  <div className="label-mono text-[9px] text-[#FFB020] mb-0.5">
                    DEEP ALPHA · {isCombined ? "SHARPS + CANDIDATES" : "SMART MONEY"} vs MARKET
                  </div>
                  <p className="text-xs text-slate-200 leading-relaxed">
                    Smart money is fading the line — backing{" "}
                    <b style={{ color: activeMetrics.alpha.side === "YES" ? "#00E599" : "#FF3B5C" }}>{activeMetrics.alpha.side}</b>{" "}
                    while the market favors {activeMetrics.alpha.marketFavorite} at{" "}
                    {Math.round((activeMetrics.alpha.favPrice || 0) * 100)}¢. Divergence edge {activeMetrics.alpha.edge}.
                  </p>
                </div>
              </div>
            )}

            <div className="flex items-center gap-2">
              <span className="mono text-xs font-semibold px-2 py-1 rounded-sm border border-[#00E59944] text-sharp bg-[#00E59912]">
                {(m?.outcomes?.[0] || "Yes")} {fmtCents(m?.prices?.[0])}
              </span>
              <span className="mono text-xs font-semibold px-2 py-1 rounded-sm border border-[#FF3B5C44] text-fade bg-[#FF3B5C12]">
                {(m?.outcomes?.[1] || "No")} {fmtCents(m?.prices?.[1])}
              </span>
              <span className="label-mono text-[8px] text-insider ml-auto border border-[#00D2FF44] rounded-sm px-1.5 py-0.5">
                {MARKET_TYPE_META[m?.marketType]?.label || m?.marketType}
              </span>
            </div>

            {/* Measurement Scope Selector */}
            <div className="flex items-center justify-between panel-2 px-3 py-2 border border-hair rounded-sm">
              <span className="label-mono text-[9px] text-retail">MEASUREMENT SCOPE</span>
              <div className="flex items-center gap-1.5">
                <button
                  data-testid="detail-measure-sharp"
                  onClick={() => setMeasureMode("sharp")}
                  className="label-mono text-[9px] px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1"
                  style={
                    !isCombined
                      ? { color: "#07090e", backgroundColor: "#00E599", borderColor: "#00E599" }
                      : { color: "#00E599", borderColor: "#00E59944", backgroundColor: "transparent" }
                  }
                >
                  <Crosshair size={11} /> SHARPS ONLY ({a?.sharpCount ?? 0})
                </button>
                <button
                  data-testid="detail-measure-combined"
                  onClick={() => setMeasureMode("combined")}
                  className="label-mono text-[9px] px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1"
                  style={
                    isCombined
                      ? { color: "#07090e", backgroundColor: "#A855F7", borderColor: "#A855F7" }
                      : { color: "#A855F7", borderColor: "#A855F744", backgroundColor: "transparent" }
                  }
                >
                  <Users size={11} /> SHARPS + CANDIDATES ({(a?.sharpCount ?? 0) + (a?.candidateCount ?? 0)})
                </button>
              </div>
            </div>

            {/* Strength + conviction */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="panel p-4">
                <SectionLabel>{isCombined ? "Smart Money Capital Share (Sharps + Candidates)" : "Sharp Money Capital Share"}</SectionLabel>
                <SmartMeter
                  strengthYes={activeMetrics?.strengthYes}
                  strengthNo={activeMetrics?.strengthNo}
                  outcomes={m?.outcomes}
                  netLean={activeMetrics?.netLean}
                  leanSide={activeMetrics?.leanSide}
                  mode={measureMode}
                />
                <div className="grid grid-cols-2 gap-2 mt-3 pt-3 border-t border-hair">
                  <Stat label={isCombined ? "Smart $ · Yes" : "Sharp $ · Yes"} value={fmtUsd(activeMetrics?.smartCapitalYes)} color="#00E599" />
                  <Stat label={isCombined ? "Smart $ · No" : "Sharp $ · No"} value={fmtUsd(activeMetrics?.smartCapitalNo)} color="#FF3B5C" />
                </div>
              </div>

              <div className="panel p-4">
                <SectionLabel>Lean &amp; Conviction</SectionLabel>
                <div className="flex items-center gap-3">
                  <LeanIcon side={activeMetrics?.leanSide} />
                  <div>
                    <div
                      data-testid="detail-composite-score"
                      className="mono text-2xl font-black"
                      style={{ color: leanColor(activeMetrics?.leanSide) }}
                    >
                      {activeMetrics?.leanSide === "UNAVAILABLE" ? "—" : activeMetrics?.leanSide === "NEUTRAL" ? "SPLIT" : `${(activeMetrics?.netLean ?? 0) > 0 ? "+" : ""}${activeMetrics?.netLean}`}
                    </div>
                    <div className="label-mono text-[9px] text-retail">
                      {activeMetrics?.leanSide === "UNAVAILABLE" ? (isCombined ? "no qualified smart signal" : "no qualified Sharp signal") : activeMetrics?.leanSide === "NEUTRAL" ? "no decisive edge" : `${activeMetrics?.leanSide} lean`}
                    </div>
                  </div>
                </div>
                <div data-testid="detail-hold-to-res-stat" className="mt-3 pt-3 border-t border-hair">
                  <div className="label-mono text-[9px] text-retail mb-1 flex items-center gap-1">
                    HISTORICAL CAPITAL RETENTION
                    <Info
                      size={10}
                      className="text-slate-600"
                      title="Original-cost retention across observed resolved positions. Sales and opposing hedges reduce continuous holding. This is a historical measurement, not a forecast."
                    />
                  </div>
                  <div className="h-2 bg-[#141a23] rounded-sm overflow-hidden border border-hair">
                    <div
                      className="h-full bg-sharp transition-all duration-700"
                      style={{ width: `${(a.holdToResolution?.ratio || 0) * 100}%` }}
                    />
                  </div>
                  <div className="mono text-[10px] text-slate-300 mt-1">{a.holdToResolution?.label}</div>
                </div>
              </div>
            </div>

            <div data-testid="detail-capital-distribution" className="panel p-4">
              <SectionLabel>Capital by Wallet Class · {a.participantCount} tracked</SectionLabel>
              <CapitalBar distribution={a.capitalDistribution} />
            </div>

            <p className="text-xs text-slate-400">Scope: {a.qualificationScope}. Qualified wallets account for {a.coverageDetail?.qualifiedCapitalPct ?? 0}% of sampled capital. {a.coverageDetail?.failedWallets ?? 0} wallet lookups unavailable. Bot exclusions apply. The combined sports track record can qualify wallets across sports.</p>
            {/* Top wallets */}
            <div className="panel p-4">
              <div className="flex items-center justify-between mb-3">
                <SectionLabel>Top Participating Wallets &amp; Tailing Breakdown</SectionLabel>
                <span className="label-mono text-[9px] text-retail">
                  {a.topWallets?.length || 0} active wallets analyzed
                </span>
              </div>
              <div className="grid grid-cols-[1fr_45px_50px_60px_65px_50px_45px_16px] gap-2 px-2 py-1.5 mb-1.5 label-mono text-[9px] font-bold text-slate-300 bg-[#0d121a] rounded-sm border border-hair">
                <span>NAME / WALLET</span>
                <span className="text-center">LEAN</span>
                <span className="text-right">ENTRY</span>
                <span className="text-center">TAIL</span>
                <span className="text-right">VOLUME</span>
                <span className="text-right">WIN%</span>
                <span className="text-right">SCORE</span>
                <span></span>
              </div>
              <div data-testid="detail-participating-wallets-table" className="space-y-1">
                {a.topWallets?.map((w) => (
                  <a
                    key={w.address}
                    data-testid="detail-wallet-row"
                    href={`/wallet/${w.address}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={w.profileStale ? `Saved wallet profile from ${new Date(w.profileUpdatedAt).toLocaleString()}` : undefined}
                    className="w-full grid grid-cols-[1fr_45px_50px_60px_65px_50px_45px_16px] gap-2 items-center px-2 py-2 rounded-sm hover:bg-[#141a23] transition-colors group text-left border border-transparent hover:border-hair cursor-pointer no-underline"
                  >
                    <div className="flex items-center gap-1.5 min-w-0">
                      <LabelSet labels={w.labels} category={w.category} />
                      <span className="mono text-[10px] text-slate-300 truncate group-hover:text-white">
                        {w.name || shortAddr(w.address)}
                      </span>
                    </div>
                    <span
                      className="mono text-[9px] font-bold text-center"
                      style={{ color: w.side === "YES" ? "#00E599" : "#FF3B5C" }}
                    >
                      {w.side}
                    </span>
                    <span className="mono text-[10px] text-slate-300 text-right">
                      {w.entryPrice != null ? `${Math.round(w.entryPrice * 100)}¢` : "—"}
                    </span>
                    <div className="flex justify-center">
                      {w.tailStatus ? (
                        <span
                          className="label-mono text-[7.5px] font-bold px-1 py-0.2 rounded-sm"
                          style={{
                            backgroundColor:
                              w.tailStatus === "BETTER_PRICE"
                                ? "rgba(0,210,255,0.15)"
                                : w.tailStatus === "PRIME_TAIL"
                                ? "rgba(0,229,153,0.15)"
                                : w.tailStatus === "ACCEPTABLE"
                                ? "rgba(251,191,36,0.15)"
                                : "rgba(255,59,92,0.15)",
                            color:
                              w.tailStatus === "BETTER_PRICE"
                                ? "#00D2FF"
                                : w.tailStatus === "PRIME_TAIL"
                                ? "#00E599"
                                : w.tailStatus === "ACCEPTABLE"
                                ? "#FBBF24"
                                : "#FF3B5C",
                            border: "1px solid currentColor",
                          }}
                        >
                          {w.tailStatus === "BETTER_PRICE"
                            ? "BETTER"
                            : w.tailStatus === "PRIME_TAIL"
                            ? "PRIME"
                            : w.tailStatus === "ACCEPTABLE"
                            ? "OK"
                            : "MOVED"}
                        </span>
                      ) : (
                        <span className="mono text-[8px] text-slate-600">—</span>
                      )}
                    </div>
                    <span className="mono text-[10px] text-slate-200 text-right">{fmtUsd(w.capital)}</span>
                    <span
                      className="mono text-[10px] text-retail text-right"
                      title={w.settledBets ? `${w.performanceScope || "Sports"}: ${w.settledBets} measured events` : "insufficient sample"}
                    >
                      {w.trueWinrate != null && (w.settledBets || 0) > 0 ? fmtPct(w.trueWinrate) : "—"}
                    </span>
                    <span className="flex justify-end">
                      <ScoreChip score={w.smartScore} />
                    </span>
                    <ArrowUpRight size={11} className="text-slate-600 group-hover:text-sharp shrink-0" />
                  </a>
                ))}
              </div>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
};

const leanColor = (side) => (side === "YES" ? "#00E599" : side === "NO" ? "#FF3B5C" : "#94A3B8");

const SectionLabel = ({ children }) => (
  <div className="label-mono text-[10px] text-slate-400 mb-3">{children}</div>
);

const Stat = ({ label, value, color }) => (
  <div>
    <div className="label-mono text-[8px] text-retail">{label}</div>
    <div className="mono text-sm font-bold" style={{ color: color || "#f1f5f9" }}>
      {value}
    </div>
  </div>
);

const LeanIcon = ({ side }) => {
  if (side === "YES") return <TrendingUp size={30} className="text-sharp" strokeWidth={2.5} />;
  if (side === "NO") return <TrendingDown size={30} className="text-fade" strokeWidth={2.5} />;
  return <Minus size={30} className="text-retail" strokeWidth={2.5} />;
};

