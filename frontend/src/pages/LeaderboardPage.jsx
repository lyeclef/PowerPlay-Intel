import { QueryError } from "@/components/terminal/QueryError";
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { Trophy, Loader2, ArrowUpRight } from "lucide-react";
import { fetchLeaderboard } from "@/lib/api";
import { fmtUsd, fmtPct, fmtSignedPct, shortAddr } from "@/lib/format";
import { LabelSet } from "@/components/terminal/LabelSet";
import { ScoreChip } from "@/components/terminal/ScoreGauge";

export default function LeaderboardPage() {
  const navigate = useNavigate();
  const [view, setView] = useState("sharp");
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["leaderboard", view],
    queryFn: () => fetchLeaderboard(60, view),
    refetchInterval: 30000,
  });

  if (isError) return <QueryError onRetry={refetch} />;

  return (
    <div>
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-1">
          <Trophy size={13} className="text-brand" />
          <span className="label-mono text-[10px] text-brand">SHARP LEADERBOARD</span>
        </div>
        <h1 className="mono text-2xl sm:text-3xl font-black tracking-tight text-white uppercase">
          Ranked by <span className="text-brand">Sharp Rank</span>
        </h1>
        <p className="text-sm text-retail mt-1">
          Sharp and Elite wallets pass sports win-rate, ROI, sample, holding and bot requirements. Results below use the named qualifying sports scope; other wallets show combined sports results.
        </p>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">{[["sharp", "Qualified Sharps"], ["proven", "Elite only"], ["candidate", "Candidates"], ["all", "All wallets"]].map(([key, label]) => <button key={key} onClick={() => setView(key)} className={`px-3 py-2 rounded-sm border text-xs ${view === key ? "text-sharp border-sharp" : "text-slate-400 border-hair"}`}>{label}</button>)}</div>
      {isLoading ? (
        <Center>
          <Loader2 className="animate-spin text-insider" size={22} />
          <span className="label-mono text-[11px] text-retail">Loading tracked wallets…</span>
        </Center>
      ) : !data?.wallets?.length ? (
        <Center>
          <span className="label-mono text-[11px] text-retail">
            No wallets currently meet this view. Inspect Candidates or All wallets while evidence is collected.
          </span>
        </Center>
      ) : (
        <div className="panel overflow-hidden">
          <div className="grid grid-cols-12 gap-2 label-mono text-[8px] text-retail px-4 py-2.5 border-b border-hair bg-surface2">
            <div className="col-span-1">#</div>
            <div className="col-span-4">Wallet</div>
            <div className="col-span-2 text-right">Sports WR</div>
            <div className="col-span-2 text-right">Sports ROI</div>
            <div className="col-span-2 text-right">Realized PnL</div>
            <div className="col-span-1 text-right">Rank</div>
          </div>
          {data.wallets.map((w, i) => (
            <motion.button
              key={w.address}
              data-testid="leaderboard-row-item"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: Math.min(i * 0.015, 0.3) }}
              onClick={() => window.open(`/wallet/${w.address}`, "_blank")}
              className="w-full grid grid-cols-12 gap-2 items-center px-4 py-2.5 border-b border-hair/60 hover:bg-[#141a23] transition-colors group text-left"
            >
              <div className="col-span-1 mono text-xs font-bold text-slate-500">
                {String(i + 1).padStart(2, "0")}
              </div>
              <div className="col-span-4 min-w-0">
                <div className="flex items-center gap-1.5">
                  <LabelSet labels={w.labels} category={w.category} />
                </div>
                <div className="mono text-[11px] text-slate-300 truncate mt-0.5 group-hover:text-white flex items-center gap-1">
                  {w.name || shortAddr(w.address)}
                  <ArrowUpRight size={11} className="text-slate-600 group-hover:text-sharp" />
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">{w.sportsRecord?.scope || "Sports"} · {w.sportsRecord?.events ?? 0} events</div>
              </div>
              <div className="col-span-2 text-right mono text-xs text-slate-200">
                {fmtPct(w.sportsRecord?.winrate, 1)}
              </div>
              <div
                className="col-span-2 text-right mono text-xs font-semibold"
                style={{ color: (w.sportsRecord?.roi || 0) >= 0 ? "#00E599" : "#FF3B5C" }}
              >
                {fmtSignedPct(w.sportsRecord?.roi, 2)}
              </div>
              <div
                className="col-span-2 text-right mono text-xs"
                style={{ color: (w.sportsRecord?.profit || 0) >= 0 ? "#00E599" : "#FF3B5C" }}
              >
                {fmtUsd(w.sportsRecord?.profit)}
              </div>
              <div className="col-span-1 text-right">
                <ScoreChip score={w.smartScore} />
              </div>
            </motion.button>
          ))}
        </div>
      )}
    </div>
  );
}

const Center = ({ children }) => (
  <div className="panel flex flex-col items-center justify-center gap-3 py-24">{children}</div>
);
