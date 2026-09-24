import { QueryError } from "@/components/terminal/QueryError";
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, ScrollText, ArrowUpRight, ArrowDownRight, Info } from "lucide-react";
import { fetchWalletAudit } from "@/lib/api";
import { fmtUsd, fmtCents, timeAgo } from "@/lib/format";

const GREEN = "#00E599";
const RED = "#FF3B5C";

export const WalletAudit = ({ address }) => {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["wallet-audit", address],
    queryFn: () => fetchWalletAudit(address, 60),
    enabled: !!address,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="panel flex items-center justify-center gap-2 py-12" data-testid="wallet-audit-loading">
        <Loader2 className="animate-spin text-insider" size={18} />
        <span className="label-mono text-[11px] text-retail">Reconstructing trade history…</span>
      </div>
    );
  }
  if (isError) return <QueryError onRetry={refetch} />;
  if (!data) return null;

  const perf = data.performance || {};
  const markets = (data.markets || []).filter((m) => m.settled && !m.void).slice(0, 12);
  const trades = data.trades || [];
  const reliable = perf.trueWinrate != null && (perf.settledBets || 0) > 0;
  const reliableRoi = perf.trueRoi != null && (perf.settledBets || 0) > 0;

  return (
    <div className="space-y-4" data-testid="wallet-audit">
      {data.reliability && <p role="status" className="text-xs text-slate-400">{data.reliability.label}: {data.reliability.note}</p>}
      {/* Honest performance header */}
      <div className="panel p-5">
        <div className="flex items-center gap-1.5 mb-3">
          <ScrollText size={13} className="text-brand" />
          <span className="label-mono text-[10px] text-slate-400">
            Audit Trail · reconstructed from {perf.sampleTrades ?? 0} real trades
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <div className="panel-2 p-3" data-testid="audit-true-winrate">
            <div className="label-mono text-[8px] text-retail mb-1">Observed Winrate</div>
            <div className="mono text-lg font-black" style={{ color: reliable ? GREEN : "#94A3B8" }}>
              {reliable ? `${Math.round(perf.trueWinrate * 100)}%` : "—"}
            </div>
            <div className="label-mono text-[8px] text-retail mt-0.5">
              {reliable
                ? `${perf.settledBets} settled bets`
                : perf.settledBets
                ? `insufficient sample (${perf.settledBets})`
                : "insufficient sample"}
            </div>
          </div>
          <div className="panel-2 p-3" data-testid="audit-net-realized">
            <div className="label-mono text-[8px] text-retail mb-1">Measured cashflow P&amp;L</div>
            <div
              className="mono text-lg font-black"
              style={{ color: (perf.netRealized || 0) >= 0 ? GREEN : RED }}
            >
              {fmtUsd(perf.netRealized)}
            </div>
            <div className="label-mono text-[8px] text-retail mt-0.5">entries vs exits + payouts</div>
          </div>
          <div className="panel-2 p-3" data-testid="audit-true-roi">
            <div className="label-mono text-[8px] text-retail mb-1">Observed cashflow ROI</div>
            <div
              className="mono text-lg font-black"
              style={{ color: reliableRoi ? ((perf.trueRoi || 0) >= 0 ? GREEN : RED) : "#94A3B8" }}
            >
              {reliableRoi ? `${perf.trueRoi >= 0 ? "+" : ""}${Math.round(perf.trueRoi * 100)}%` : "—"}
            </div>
            <div className="label-mono text-[8px] text-retail mt-0.5">
              {reliableRoi ? `on ${fmtUsd(perf.invested)} invested` : "insufficient sample"}
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-start gap-2 rounded-sm border border-hair bg-surface2/40 px-2.5 py-2">
          <Info size={12} className="text-insider mt-0.5 shrink-0" />
          <p className="text-[11px] text-retail leading-relaxed">
            These measurements use the same saved evaluation as the wallet profile. Valid cashflows remain visible even when other records are incomplete. Fees absent from source records are not invented; Missing fee evidence does not block Sharp eligibility.
          </p>
        </div>
      </div>

      {/* Per-market P&L breakdown */}
      {markets.length > 0 && (
        <div className="panel p-5" data-testid="audit-market-breakdown">
          <div className="label-mono text-[10px] text-slate-400 mb-3">
            Biggest Settled Bets — Net P&amp;L
          </div>
          <div className="space-y-1">
            {markets.map((m, i) => (
              <div key={m.conditionId || i} className="flex items-center gap-2 py-1">
                {m.icon && <img src={m.icon} alt="" className="h-4 w-4 rounded-sm object-cover shrink-0" />}
                <span className="text-[11px] text-slate-300 truncate flex-1">{m.title || "—"}</span>
                <span className="mono text-[10px] text-retail w-16 text-right">
                  {fmtUsd(m.invested)} in
                </span>
                <span
                  className="mono text-[11px] font-bold w-20 text-right"
                  style={{ color: m.netPnl >= 0 ? GREEN : RED }}
                >
                  {m.netPnl >= 0 ? "+" : ""}
                  {fmtUsd(m.netPnl)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Raw trade tape */}
      <div className="panel p-5" data-testid="audit-raw-trades">
        <div className="label-mono text-[10px] text-slate-400 mb-3">
          Raw Trades · most recent {trades.length}
        </div>
        <div className="divide-y divide-hair max-h-[420px] overflow-y-auto">
          {trades.map((t, i) => {
            const buy = t.side === "BUY";
            const color = buy ? GREEN : RED;
            const Arrow = buy ? ArrowUpRight : ArrowDownRight;
            return (
              <div key={i} className="grid grid-cols-[52px_1fr_96px_88px] gap-2 items-center py-1.5">
                <span className="mono text-[9px] text-retail">
                  {timeAgo(new Date(t.timestamp * 1000).toISOString())}
                </span>
                <div className="flex items-center gap-1.5 min-w-0">
                  {t.icon && <img src={t.icon} alt="" className="h-3.5 w-3.5 rounded-sm object-cover shrink-0" />}
                  <span className="text-[11px] text-slate-300 truncate">{t.question}</span>
                </div>
                <div className="flex items-center justify-end gap-1 mono text-[10px] font-bold" style={{ color }}>
                  <Arrow size={11} strokeWidth={2.5} />
                  {t.side} {t.outcome}
                  <span className="text-retail ml-0.5">@{fmtCents(t.price)}</span>
                </div>
                <span className="mono text-[11px] font-bold text-slate-200 text-right">
                  {fmtUsd(t.sizeUsd)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
