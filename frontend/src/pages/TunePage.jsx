import React, { useEffect, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { fetchThresholds, saveThresholds, resetThresholds, fetchValidation } from "@/lib/api";
import { QueryError } from "@/components/terminal/QueryError";
import { Metric } from "@/components/terminal/QualificationEvidence";

const groups = {
  weights: ["Sharp ranking weights", "Applies only after qualification. Win rate 40%, observed ROI 40%, track-record depth 20%."],
  sharp: ["Sharp requirements", "Both position and original-capital retention must reach 90%. Changing weights cannot bypass this rule."],
  proven: ["Elite requirements", "Same performance and holding rules, with at least 300 events and 180 days. Future tracking does not block qualification."],
  candidate: ["Candidate discovery", "Promising observed holders remain candidates while qualification evidence is incomplete."],
  automation: ["Automation evidence", "Two strong evidence groups imply probable automation. One group means uncertain; volume, rewards or overnight fills alone do not prove a bot."],
  whale: ["Whale size tag", "Size never implies skill and never grants Sharp eligibility."]
};
const labels = { winrate: "Win-rate weight", roi: "ROI weight", minWinrate: "Minimum win rate (0.50 = 50%)", minRoi: "ROI must exceed (0 = positive ROI)", minDataCoverage: "Minimum measured event coverage", price: "Price-quality weight", returns: "Return weight", repeatability: "Repeatability weight", depth: "Evidence-depth weight", minEvents: "Minimum distinct events", minDays: "Minimum elapsed days", minHold: "Minimum retention (0.90 = 90%)", windowDays: "Default window in days", extendedDays: "Extended window in days", recentEvents: "Recent holding check: events", confidence: "One-sided confidence", minCoverage: "Minimum CLV coverage", minClvDays: "Minimum CLV day blocks", futureEvents: "Minimum untouched future events", maxAgeSeconds: "Maximum quote age before start (seconds)", maxSpread: "Maximum bid–ask spread", minDepthUsd: "Minimum nearby depth ($)", startBufferSeconds: "Buffer before reported start (seconds)", pollSeconds: "Quote polling interval (seconds)", minDailyEpisodes: "Minimum daily episodes for timing checks", rapidSeconds: "Rapid episode gap (seconds)", rapidFraction: "Required rapid-episode fraction", intenseEpisodes: "High-intensity daily episodes", intenseMarkets: "High-intensity distinct markets", hedgedFraction: "Paired inventory fraction", minHedgedMarkets: "Minimum paired markets", maxTradeUsd: "Largest trade threshold ($)", totalVolume: "Volume threshold ($)", portfolioValue: "Portfolio-value threshold ($)", avgTradeUsd: "Average episode threshold ($)" };

export default function TunePage() {
  const qc = useQueryClient();
  const [cfg, setCfg] = useState(null);
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");
  const query = useQuery({queryKey: ["thresholds"], queryFn: fetchThresholds});
  const validation = useQuery({queryKey: ["validation"], queryFn: fetchValidation, refetchInterval: 30000});
  useEffect(() => { if (query.data?.config) setCfg(query.data.config); }, [query.data]);
  const mutation = useMutation({mutationFn: reset => reset ? resetThresholds(token) : saveThresholds(cfg, token), onSuccess: data => {setCfg(data.config); setMessage(`Rules saved. ${data.queued || 0} wallets queued for new evaluation. Old scores are excluded.`); qc.invalidateQueries();}, onError: e => setMessage(e.response?.data?.detail || "Could not save configuration")});
  if (query.isError) return <QueryError onRetry={query.refetch} />;
  const v = validation.data;
  return <div className="max-w-5xl mx-auto space-y-5 pb-8" data-testid="tune-page">
    <div><div className="label-mono text-brand text-xs">CLASSIFICATION RULES & VALIDATION</div><h1 className="text-3xl font-black text-white mt-2">Sports results. Holding. Track record.</h1><p className="text-sm text-slate-400 mt-2">Every Sharp must retain at least 90% through resolution. Sharp qualification uses win rate, ROI and enough resolved sports events. No price or fee-evidence requirements.</p></div>
    <div className="panel p-5" data-testid="validation-status">
      <h2 className="text-sm font-bold text-white mb-2">Optional future tracking</h2>
      {validation.isError ? <p className="text-sm text-amber-300">Validation status is temporarily unavailable.</p> : !v ? <p className="text-sm text-slate-400">Loading observation status…</p> : <>
        <p className="text-xs text-slate-400 mb-4">{v.status} · {v.ruleVersion}. Baselines include candidates, losing wallets and excluded styles. Changing rules creates a new baseline; prior evidence is retained separately.</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3"><Metric label="Enrolled wallets" value={v.enrolledWallets} /><Metric label="Daily snapshots" value={v.savedDailySnapshots} /><Metric label="Future event samples" value={v.futureScopeSamples} /><Metric label="Passed future scopes" value={v.passedFutureScopes} /></div>
        <p className="text-xs text-slate-400 mt-3">These observations do not gate Sharp qualification. Wallet tracking requires the local backend to stay running.</p>
        <ul className="list-disc pl-4 mt-3 text-xs text-slate-500 space-y-1">{v.limitations?.map(l => <li key={l}>{l}</li>)}</ul>
      </>}
    </div>
    {cfg && Object.entries(groups).map(([group, [title, note]]) => <details key={group} open={group === "sharp" || group === "weights"} className="panel p-5">
      <summary className="text-sm font-bold text-white cursor-pointer">{title}</summary><p className="text-xs text-slate-400 mt-2 mb-4">{note}</p>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">{Object.entries(cfg[group] || {}).filter(([field]) => group !== "proven" || ["minEvents", "minDays"].includes(field)).map(([field, value]) => <label key={field} className="text-xs text-slate-400">{labels[field] || field}<input aria-label={`${title}: ${labels[field] || field}`} type="number" step="any" min={field === "minHold" ? .9 : 0} value={value} onChange={e => setCfg(c => ({...c, [group]: {...c[group], [field]: Number(e.target.value)}}))} className="block w-full mt-1 p-2 bg-surface2 border border-hair text-slate-100 rounded-sm" /></label>)}</div>
    </details>)}
    <div className="panel p-4 flex flex-wrap items-end gap-3"><label className="text-xs text-slate-400">Local admin token<input aria-label="Local admin token" type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" className="block p-2 mt-1 bg-surface2 border border-hair rounded-sm" /></label><button onClick={() => mutation.mutate(false)} disabled={!token || mutation.isPending} className="px-4 py-2 bg-brand text-white rounded-sm disabled:opacity-50">Apply rules</button><button onClick={() => mutation.mutate(true)} disabled={!token || mutation.isPending} className="px-4 py-2 border border-hair text-slate-300 rounded-sm disabled:opacity-50">Reset defaults</button><p role="status" className="text-xs text-slate-300 w-full">{message}</p></div>
  </div>;
}

