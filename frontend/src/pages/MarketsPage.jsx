import { QueryError } from "@/components/terminal/QueryError";
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Gamepad2, Trophy, Search, X, Zap, Crosshair, Users } from "lucide-react";
import { fetchCategories, fetchMarkets, searchMarkets, MARKET_TYPE_META, pickDisplayMarket } from "@/lib/api";
import { EventCard } from "@/components/terminal/EventCard";
import { MarketDetail } from "@/components/terminal/MarketDetail";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";

const TAB_TESTIDS = {
  esports: "filter-tab-esports",
  all: "filter-tab-all",
  soccer: "filter-tab-soccer",
  nfl: "filter-tab-nfl",
  cfb: "filter-tab-cfb",
  nba: "filter-tab-nba",
  mma: "filter-tab-mma",
};
const TYPES = ["moneyline", "spread", "total", "prop"];

export default function MarketsPage() {
  const [category, setCategory] = useState("esports");
  const [marketType, setMarketType] = useState("moneyline");
  const [measureMode, setMeasureMode] = useState("sharp"); // "sharp" | "combined"
  const [alphaOnly, setAlphaOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [selEvent, setSelEvent] = useState(null);
  const [selId, setSelId] = useState(null);
  const [open, setOpen] = useState(false);

  const { data: categories } = useQuery({
    queryKey: ["categories"],
    queryFn: fetchCategories,
    staleTime: Infinity,
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["markets", category, marketType],
    queryFn: () => fetchMarkets(category, marketType, 30),
    refetchInterval: 12000,
    enabled: !submitted,
  });

  const { data: searchData, isLoading: searchLoading, isError: searchError, refetch: retrySearch } = useQuery({
    queryKey: ["search", submitted],
    queryFn: () => searchMarkets(submitted, 24),
    enabled: !!submitted,
    refetchInterval: 12000,
  });

  const esports = (categories || []).filter((c) => c.group === "esports");
  const sports = (categories || []).filter((c) => c.group === "sports");

  const events = submitted ? searchData?.events : data?.events;
  const loading = submitted ? searchLoading : isLoading;
  const isCombined = measureMode === "combined";
  const shownEvents = (submitted ? events || [] : (events || []).filter((ev) => ev.typeCounts?.[marketType]))
    .filter((ev) => {
      if (!alphaOnly) return true;
      const d = pickDisplayMarket(ev, marketType);
      const a = isCombined && d?.analysis?.combined ? d.analysis.combined : d?.analysis;
      return a?.alpha?.divergent;
    });

  const openEvent = (event, id) => {
    setSelEvent(event);
    setSelId(id);
    setOpen(true);
  };

  const doSearch = (e) => {
    e?.preventDefault();
    setSubmitted(query.trim());
  };
  const clearSearch = () => {
    setQuery("");
    setSubmitted("");
  };

  const hasEvents = Boolean(events && events.length > 0);
  const queryFailed = submitted ? searchError : isError;

  if (queryFailed && !hasEvents) {
    return <QueryError onRetry={submitted ? retrySearch : refetch} />;
  }

  return (
    <div>
      {/* Hero */}
      <div className="mb-4">
        <div className="flex items-center gap-2 mb-1">
          <span className="h-1.5 w-1.5 rounded-full bg-brand pulse-dot" />
          <span className="label-mono text-[10px] text-brand">MARKETS MONITOR · POLYMARKET LIVE</span>
        </div>
        <h1 className="mono text-2xl sm:text-3xl font-black tracking-tight text-white uppercase">
          Where the <span className="text-brand">Smart Money</span> Sits
        </h1>
        <p className="text-sm text-retail mt-1 max-w-2xl">
          Each side reflects the <span className="text-slate-300">share of qualified Sharp capital</span> among sampled top holders.
        </p>
      </div>

      {/* Search */}
      <form onSubmit={doSearch} className="relative mb-3">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-retail" />
        <Input
          data-testid="global-search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search matches & events (e.g. FaZe, Navi, Lakers)…"
          className="mono text-xs bg-surface border-hair text-slate-200 h-10 pl-9 pr-20 focus-visible:ring-brand"
        />
        {submitted ? (
          <button
            type="button"
            onClick={clearSearch}
            data-testid="search-clear-btn"
            className="absolute right-2 top-1/2 -translate-y-1/2 label-mono text-[10px] text-fade flex items-center gap-1 px-2 py-1"
          >
            <X size={12} /> CLEAR
          </button>
        ) : (
          <button
            type="submit"
            data-testid="search-submit-btn"
            className="absolute right-2 top-1/2 -translate-y-1/2 label-mono text-[10px] text-brand px-2 py-1"
          >
            SEARCH
          </button>
        )}
      </form>

      {/* Market-type filter + Deep Alpha */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        <span className="label-mono text-[9px] text-retail px-1">MARKET TYPE</span>
        {TYPES.map((t) => {
          const active = marketType === t;
          return (
            <button
              key={t}
              data-testid={`filter-type-${t}`}
              onClick={() => setMarketType(t)}
              className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors"
              style={
                active
                  ? { color: "#07090e", backgroundColor: "#00D2FF", borderColor: "#00D2FF" }
                  : { color: "#94A3B8", borderColor: "#1E2633" }
              }
            >
              {MARKET_TYPE_META[t]?.label}
            </button>
          );
        })}
        <div className="h-5 w-px bg-hair mx-1" />
        <button
          data-testid="filter-alpha-toggle"
          onClick={() => setAlphaOnly((v) => !v)}
          className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1"
          style={
            alphaOnly
              ? { color: "#07090e", backgroundColor: "#FFB020", borderColor: "#FFB020" }
              : { color: "#FFB020", borderColor: "#FFB02055" }
          }
        >
          <Zap size={11} /> DEEP ALPHA
        </button>
        <div className="h-5 w-px bg-hair mx-1" />
        <span className="label-mono text-[9px] text-retail px-1">MEASUREMENT</span>
        <button
          data-testid="measure-mode-sharp"
          onClick={() => setMeasureMode("sharp")}
          className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1"
          style={
            measureMode === "sharp"
              ? { color: "#07090e", backgroundColor: "#00E599", borderColor: "#00E599" }
              : { color: "#00E599", borderColor: "#00E59944", backgroundColor: "transparent" }
          }
        >
          <Crosshair size={11} /> SHARPS ONLY
        </button>
        <button
          data-testid="measure-mode-combined"
          onClick={() => setMeasureMode("combined")}
          className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors flex items-center gap-1"
          style={
            measureMode === "combined"
              ? { color: "#07090e", backgroundColor: "#A855F7", borderColor: "#A855F7" }
              : { color: "#A855F7", borderColor: "#A855F744", backgroundColor: "transparent" }
          }
        >
          <Users size={11} /> SHARPS + CANDIDATES
        </button>
      </div>

      {/* Category tabs (hidden during search) */}
      {!submitted && (
        <div className="panel p-2 mb-5 flex flex-wrap items-center gap-1.5">
          <TabGroup icon={Gamepad2} label="ESPORTS" cats={esports} current={category} onPick={setCategory} accent="#A855F7" />
          <div className="h-5 w-px bg-hair mx-1 hidden sm:block" />
          <TabGroup icon={Trophy} label="SPORTS" cats={sports} current={category} onPick={setCategory} accent="#00D2FF" />
        </div>
      )}

      {queryFailed && hasEvents && (
        <div role="status" className="panel p-2.5 mb-4 flex items-center justify-between text-xs text-amber-300 border border-amber-500/30 bg-amber-500/10">
          <span>Live connection hiccup · Showing saved figures</span>
          <button type="button" onClick={submitted ? retrySearch : refetch} className="underline text-amber-200 hover:text-white font-semibold ml-2">
            Retry now
          </button>
        </div>
      )}

      {submitted && (
        <div className="mb-4 flex items-center gap-2">
          <span className="label-mono text-[10px] text-insider">
            SEARCH RESULTS · "{submitted}" · {shownEvents.length} events
          </span>
        </div>
      )}

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full bg-surface2 rounded-sm" />
          ))}
        </div>
      ) : shownEvents.length ? (
        <div data-testid="markets-table-container" className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {shownEvents.map((ev, i) => (
            <EventCard key={ev.eventSlug} event={ev} marketType={marketType} measureMode={measureMode} index={i} onOpen={openEvent} />
          ))}
        </div>
      ) : (
        <div className="panel p-16 text-center">
          <p className="label-mono text-xs text-retail">
            {alphaOnly
              ? "No divergence signals right now — smart money agrees with the market here."
              : submitted
              ? "No matching sports/esports events."
              : `No ${MARKET_TYPE_META[marketType]?.label?.toLowerCase() || ""} markets in this category right now.`}
          </p>
        </div>
      )}

      <MarketDetail event={selEvent} initialId={selId} open={open} onOpenChange={setOpen} initialMode={measureMode} />
    </div>
  );
}

const TabGroup = ({ icon: Icon, label, cats, current, onPick, accent }) => (
  <div className="flex flex-wrap items-center gap-1.5">
    <span className="flex items-center gap-1 label-mono text-[9px] px-1.5" style={{ color: accent }}>
      <Icon size={12} /> {label}
    </span>
    {cats.map((c) => {
      const active = current === c.id;
      return (
        <button
          key={c.id}
          title={c.title || c.label}
          data-testid={TAB_TESTIDS[c.id] || `filter-tab-${c.id}`}
          onClick={() => onPick(c.id)}
          className="label-mono text-[10px] px-2.5 py-1 rounded-sm border transition-colors"
          style={
            active
              ? { color: "#07090e", backgroundColor: accent, borderColor: accent }
              : { color: "#94A3B8", borderColor: "#1E2633", backgroundColor: "transparent" }
          }
        >
          {c.label}
        </button>
      );
    })}
  </div>
);
