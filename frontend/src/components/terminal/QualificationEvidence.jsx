import React, { useState } from "react";
import { CheckCircle2, CircleDashed, ShieldQuestion } from "lucide-react";
import { fmtUsd } from "@/lib/format";
import { walletMeta } from "@/lib/meta";

const pct = v => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const date = t => t ? new Date(t * 1000).toLocaleDateString() : "unavailable";

export function QualificationEvidence({ evidence }) {
  const scopes = evidence?.scopes || {};
  const [selected, setSelected] = useState("");
  const key = scopes[selected] ? selected : (scopes[evidence?.bestScopeKey] && ["SHARP", "PROVEN_SHARP"].includes(evidence.category) ? evidence.bestScopeKey : (scopes.Sports ? "Sports" : Object.keys(scopes).sort((a, b) => scopes[b].events - scopes[a].events)[0]));
  const scope = scopes[key];
  const holding = evidence?.holding || {};
  const auto = evidence?.automation || {};
  if (!evidence) return null;
  return <div className="space-y-4" data-testid="qualification-evidence">
    <div className="panel p-5">
      <h2 className="label-mono text-xs text-brand mb-2">Mandatory holding requirement · 90% minimum</h2>
      <p className="text-xs text-slate-400 mb-4">At least 90% of resolved positions must retain 90% of their originally acquired directional shares. At least 90% of entry-cost capital must also survive. Early sales and opposing hedges reduce retention; re-entry starts a new lot. Sports and esports are combined for the overall record. Live bets count; non-sports markets do not.</p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Positions held ≥90%" value={pct(holding.positionRate)} sub={`${holding.heldPositions || 0} of ${holding.measuredPositions || 0} measured`} />
        <Metric label="Capital retained" value={pct(holding.capitalRate)} sub={`${fmtUsd(holding.retainedCost)} retained at original cost`} />
        <Metric label="Position bounds" value={holding.positionLower == null ? "Unknown" : `${pct(holding.positionLower)}–${pct(holding.positionUpper)}`} sub="Includes unknown positions in known cohort" />
        <Metric label="Capital bounds" value={holding.capitalLower == null ? "Unknown" : `${pct(holding.capitalLower)}–${pct(holding.capitalUpper)}`} sub="Unknown history is not assumed held" />
      </div>
      <p className="text-xs text-slate-400 mt-3">Measured coverage: {pct(holding.coverage)} · {evidence.pendingPositions || 0} pending positions · {evidence.voidPositions || 0} void/refund positions excluded. Holdings include losses and delayed redemptions.</p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
        <Metric label="Pre-match entry cost" value={fmtUsd(evidence.timing?.prematchCost)} />
        <Metric label="Live entry cost" value={fmtUsd(evidence.timing?.liveCost)} />
        <Metric label="Known post-result cost" value={fmtUsd(evidence.timing?.postResultCost)} />
        <Metric label="Unknown entry timing" value={fmtUsd(evidence.timing?.unknownCost)} />
      </div>
    </div>
    <div className="panel p-5">
      <h2 className="label-mono text-xs text-brand mb-3">Sports track record · win rate & ROI</h2>
      {Object.keys(scopes).length ? <>
        <label className="text-xs text-slate-400">Evidence scope
          <select aria-label="Evidence scope" value={key} onChange={e => setSelected(e.target.value)} className="block w-full mt-1 mb-4 p-2 bg-surface2 border border-hair rounded-sm text-slate-200">
            {Object.keys(scopes).map(k => <option key={k} value={k}>{k} · {walletMeta(scopes[k].category).label} · {scopes[k].events} events</option>)}
          </select>
        </label>
        <p className="text-xs text-slate-400 mb-4">{scope.scope} · {date(scope.windowStart)}–{date(scope.windowEnd)} · {scope.windowDays}-day window · {scope.elapsedDays} elapsed days. Related markets count as one event.</p>
        <div className="space-y-3">{scope.gates.map(g => <div key={g.key} className="flex gap-2 items-start">
          {g.passed ? <CheckCircle2 size={15} className="text-sharp mt-0.5 shrink-0" /> : <CircleDashed size={15} className="text-amber-400 mt-0.5 shrink-0" />}
          <div><div className="text-xs text-slate-200">{g.passed ? "Pass" : "Not established"} · {g.label}</div><p className="text-xs text-slate-400 mt-0.5">{g.detail}</p></div>
        </div>)}</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5">
          <Metric label="Sports event win rate" value={pct(scope.winrate)} sub={`${scope.wins || 0} profitable / ${scope.events || 0} measured events`} />
          <Metric label="Sports ROI" value={pct(scope.roi)} sub="Observed P&L / invested capital" />
          <Metric label="Sports P&L" value={fmtUsd(scope.profit)} sub="Related positions netted per event" />
          <Metric label="Measured event coverage" value={pct(scope.dataCoverage)} sub={scope.sampledHistory ? "Sampled history; source feed capped" : "Fetched history"} />
          <Metric label="Recent capital retention" value={pct(scope.recentHolding?.capitalRate)} sub="Most recent resolved events" />
          <Metric label="Standardized event ROI" value={pct(scope.standardizedRoi)} sub="Equal stake per event; diagnostic" />
          <Metric label="P&L without best event" value={fmtUsd(scope.profitWithoutBestEvent)} sub="Concentration diagnostic" />
        </div>
        <div className="mt-4 border-t border-hair pt-4">
          <h3 className="label-mono text-[10px] text-slate-300 mb-2">Chronological consistency</h3>
          <div className="grid grid-cols-2 gap-3">{scope.periods?.map((p, i) => <Metric key={i} label={i ? "Recent half" : "Early half"} value={fmtUsd(p.pnl)} sub={`${p.events} events · WR ${pct(p.winrate)}`} />)}</div>
          <p className="text-xs text-slate-400 mt-3">Future validation: {scope.forward?.status?.replaceAll("_", " ")} · {scope.forward?.events || 0} new events since {date(scope.forward?.baselineAt)}. Future tracking is informational and does not block Sharp or Elite. Historical status: {scope.historicalCategory ? walletMeta(scope.historicalCategory).label : "not yet observed"}.</p>
        </div>
      </> : <p className="text-sm text-slate-400">No grouped sports evidence is available yet. Measured cashflows remain visible below.</p>}
      <p className="text-xs text-slate-500 mt-4">Qualification uses sports win rate, ROI, track-record size, 90% retention and bot screening. Closing-price data, fee evidence and pre-match timing are not requirements.</p>
    </div>
    <div className="panel p-5">
      <h2 className="label-mono text-xs text-brand mb-2 flex items-center gap-2"><ShieldQuestion size={15} />Automation and trading style</h2>
      <p className="text-sm text-slate-200 mb-3">{auto.risk === "low_observed" ? "Low observed automation risk" : auto.risk === "high" ? "Probable bot · multiple evidence groups" : "Automation uncertain · excluded from Sharp ranking"}</p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Estimated execution episodes" value={auto.estimatedEpisodes} sub={`${auto.activeDays || 0} active days`} />
        <Metric label="Hedged capital" value={pct(auto.hedgedCapitalRatio)} sub="Paired opposing acquisitions" />
        <Metric label="Incentive income" value={fmtUsd(auto.incentiveIncome)} sub="Separate from directional returns" />
        <Metric label="Evidence groups" value={auto.groups?.length || 0} sub="Two strong groups required for probable bot" />
      </div>
      {auto.groups?.map(g => <p className="text-xs text-amber-300 mt-2" key={g.group}>{g.reason}</p>)}
      <p className="text-xs text-slate-400 mt-3">{auto.note}</p>
    </div>
    <p className="text-[11px] text-slate-500">Rule {evidence.ruleVersion} · evaluated {new Date(evidence.asOf * 1000).toLocaleString()}.{evidence.sourceObservedAt && <> Source snapshot: {new Date(evidence.sourceObservedAt * 1000).toLocaleString()}.</>} Sports track-record rules; future tracking is informational.</p>
  </div>;
}

export const Metric = ({label, value, sub}) => <div className="bg-surface2/40 border border-hair rounded-sm p-3 min-w-0"><div className="label-mono text-[8px] text-retail mb-1">{label}</div><div className="mono text-sm font-bold text-slate-100 break-words">{value ?? "—"}</div>{sub && <p className="text-[10px] text-slate-500 mt-1">{sub}</p>}</div>;
