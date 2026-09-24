import React from "react";
import { Clock, Loader2, AlertTriangle } from "lucide-react";
import { timeAgo } from "@/lib/format";

export const profilingActive = status => ["waiting", "profiling"].includes(status?.state);

export function ProfilingStatus({ status, analysis, onRetry }) {
  const state = status?.state || (analysis ? "ready" : "not_started");
  const active = profilingActive(status);
  const failed = status?.failedWallets ?? analysis?.coverageDetail?.failedWallets ?? 0;
  const cached = status?.cachedWallets ?? analysis?.cachedWallets ?? 0;
  const total = status?.totalWallets;
  let text;
  if (state === "waiting") text = status?.priority === "opened_market" ? "Priority queue · starting next" : "Waiting in queue · open to prioritize";
  else if (state === "profiling") text = total != null ? `Profiling · ${status.completedWallets ?? 0}/${total} wallets checked` : "Loading current market holders…";
  else if (state === "error") text = "Profiling interrupted · available results retained";
  else if (state === "not_started") text = "Not started · open to analyze";
  else if (failed) text = `Results available · ${failed} wallet${failed === 1 ? "" : "s"} unavailable`;
  else if (analysis) text = `Updated ${timeAgo(analysis.updatedAt)}`;
  const Icon = state === "error" ? AlertTriangle : state === "profiling" ? Loader2 : Clock;
  return <div data-testid="profiling-status" role="status" className="text-[10px] text-slate-400 py-2 space-y-1">
    <div className="flex items-center gap-1.5"><Icon size={12} className={state === "profiling" ? "animate-spin text-insider" : ""} /><span>{text}</span></div>
    {active && analysis && <p>{analysis.pendingWallets ? "Partial results · signal may change as wallets finish." : `Showing saved results from ${new Date(analysis.updatedAt).toLocaleString()} while refreshing.`}</p>}
    {cached > 0 && <p>{cached} wallet profile{cached === 1 ? "" : "s"} use saved data, at most 24 hours old.</p>}
    {(state === "error" || (state === "ready" && failed > 0)) && onRetry && <button type="button" onClick={onRetry} className="text-brand underline">Retry profiling</button>}
  </div>;
}
