import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ScanSearch, Loader2, AlertTriangle, ShieldCheck, ShieldAlert, ShieldQuestion } from "lucide-react";
import { fetchWallet } from "@/lib/api";
import { fmtUsd, fmtPct, fmtSignedPct, shortAddr } from "@/lib/format";
import { ScoreGauge } from "@/components/terminal/ScoreGauge";
import { LabelSet } from "@/components/terminal/LabelSet";
import { QualificationEvidence } from "@/components/terminal/QualificationEvidence";
import { WalletHistory } from "@/components/terminal/WalletHistory";
import { WalletAudit } from "@/components/terminal/WalletAudit";
import { walletMeta } from "@/lib/meta";
import { Input } from "@/components/ui/input";

const EXAMPLES = [
  "0x73e3fec494611d73c170cb2f23850fd998b21be9",
  "0xd27fea6edbc13943c430cd7d1bc5ae0cc59c13af",
];

export default function WalletClassifierPage() {
  const { address } = useParams();
  const navigate = useNavigate();
  const [input, setInput] = useState(address || "");

  useEffect(() => {
    setInput(address || "");
  }, [address]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["wallet", address],
    queryFn: () => fetchWallet(address),
    enabled: !!address,
    retry: false,
  });

  const submit = (e) => {
    e?.preventDefault();
    const v = input.trim().toLowerCase();
    if (v.startsWith("0x") && v.length === 42) navigate(`/wallet/${v}`);
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-1">
          <ScanSearch size={13} className="text-brand" />
          <span className="label-mono text-[10px] text-brand">WALLET CLASSIFIER LAB</span>
        </div>
        <h1 className="mono text-2xl sm:text-3xl font-black tracking-tight text-white uppercase">
          Profile Any <span className="text-brand">Wallet</span>
        </h1>
        <p className="text-sm text-retail mt-1">
          Paste a Polymarket proxy wallet address to diagnose its archetype, edge and hold behavior.
        </p>
      </div>

      <form onSubmit={submit} className="panel p-3 flex flex-col sm:flex-row gap-2 mb-3">
        <Input
          data-testid="wallet-inspect-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="0x… proxy wallet address"
          className="mono text-xs bg-surface2 border-hair text-slate-200 h-10 focus-visible:ring-sharp"
        />
        <button
          data-testid="wallet-inspect-btn"
          type="submit"
          className="h-10 px-5 rounded-sm bg-brand text-white label-mono text-[11px] font-bold hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
        >
          <ScanSearch size={14} /> INSPECT
        </button>
      </form>

      <div className="flex items-center gap-2 mb-6">
        <span className="label-mono text-[9px] text-retail">TRY:</span>
        {EXAMPLES.map((a) => (
          <button
            key={a}
            onClick={() => navigate(`/wallet/${a}`)}
            className="mono text-[10px] text-insider hover:underline"
          >
            {shortAddr(a)}
          </button>
        ))}
      </div>

      {!address ? null : isLoading ? (
        <div className="panel flex flex-col items-center justify-center gap-3 py-24">
          <Loader2 className="animate-spin text-insider" size={22} />
          <span className="label-mono text-[11px] text-retail">Scanning on-chain history…</span>
        </div>
      ) : isError || !data ? (
        <div className="panel flex flex-col items-center justify-center gap-2 py-20 text-fade">
          <AlertTriangle size={20} />
          <span className="label-mono text-[11px]" data-testid="wallet-error-message">
            {error?.response?.status === 429
              ? "Too many lookups — rate limited. Wait a minute and try again."
              : "Could not profile that address."}
          </span>
        </div>
      ) : (
        <WalletCard data={data} />
      )}
    </div>
  );
}

const WalletCard = ({ data }) => {
  const m = walletMeta(data.category);
  const s = data.stats || {};
  const comp = data.scoreComponents || {};
  const reliableWr = s.true_winrate != null && (s.settled_bets || 0) > 0;
  const reliableRoi = s.true_roi != null && (s.settled_bets || 0) > 0;
  return (
    <div className="space-y-4">
      <div data-testid="wallet-score-card" className="panel p-5 grid grid-cols-1 md:grid-cols-[auto_1fr] gap-6 items-center">
        <div className="flex flex-col items-center gap-2">
          <ScoreGauge score={data.smartScore} testId="wallet-score-gauge" />
          <div className="flex items-center gap-1.5" data-testid="wallet-category-badge">
            <LabelSet labels={data.labels} category={data.category} />
          </div>
          <span className="label-mono text-[9px] text-retail">
            HOLDING COVERAGE {Math.round((data.confidence || 0) * 100)}%
          </span>
          <ReliabilityBadge reliability={data.reliability} />
        </div>

        <div>
          <div className="mono text-sm text-slate-200 mb-0.5">
            {data.name || data.pseudonym || shortAddr(data.address)}
          </div>
          <div className="mono text-[10px] text-retail mb-3 break-all">{data.address}</div>
          <p className="text-sm leading-relaxed mb-3" style={{ color: m.color }}>
            {m.blurb}
          </p>
          <ul className="space-y-1">
            {data.reasons?.map((r, i) => (
              <li key={i} className="text-xs text-slate-300 flex items-start gap-2">
                <span className="mt-1 h-1 w-1 rounded-full shrink-0" style={{ backgroundColor: m.color }} />
                {r}
              </li>
            ))}
          </ul>
          {data.automation && (
            <div
              data-testid="wallet-automation-note"
              className="mt-3 flex items-start gap-2 rounded-sm border border-mm/40 bg-mm/10 px-2.5 py-2"
            >
              <span className="mono text-[11px] text-mm shrink-0">⚠</span>
              <p className="text-[11px] text-mm/90 leading-relaxed">
                Automation concerns exclude this wallet from Sharp ranking. Its measured returns and holding metrics remain visible below.
              </p>
            </div>
          )}
        </div>
      </div>

      <QualificationEvidence evidence={data.evidence} />
      <div className="panel p-5">
        <div className="label-mono text-[10px] text-slate-400 mb-3">Sharp ranking · qualified wallets only</div>
        <p className="text-xs text-slate-400 mb-3">{data.scoreNote} This index is not a win probability.</p>
        {Object.entries(data.scoreWeights || {}).map(([key, weight]) => <CompBar key={key} label={`${key.toUpperCase()} (${Math.round(weight * 100)}%)`} value={comp[key]} />)}
      </div>
      <p className="text-xs text-slate-400">All-market cashflow results below cover reconciled settled or fully exited positions, including non-sports markets. Sharp qualification uses the sports record above.</p>
      {/* Stats grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCell
          label="All-market winrate"
          value={reliableWr ? fmtPct(s.true_winrate) : "—"}
          color={reliableWr ? "#00E599" : undefined}
          sub={reliableWr ? `${s.settled_bets} settled bets` : (s.settled_bets ? `insufficient sample (${s.settled_bets})` : "insufficient sample")}
        />
        <StatCell
          label="All-market cashflow ROI"
          value={reliableRoi ? fmtSignedPct(s.true_roi) : "—"}
          color={reliableRoi ? ((s.true_roi || 0) >= 0 ? "#00E599" : "#FF3B5C") : undefined}
          sub={reliableRoi ? "netted" : "insufficient sample"}
        />
        <StatCell label="Measured cashflow P&L" value={fmtUsd(s.net_realized)} color={(s.net_realized || 0) >= 0 ? "#00E599" : "#FF3B5C"} />
        <StatCell label="Unrealized PnL" value={fmtUsd(s.unrealized_pnl)} color={(s.unrealized_pnl || 0) >= 0 ? "#00E599" : "#FF3B5C"} />
        <StatCell label="Total Volume" value={fmtUsd(s.total_volume)} />
        <StatCell label="Estimated episodes" value={s.n_logical ?? s.n_trades} />
        <StatCell label="Markets" value={s.distinct_markets} />
      </div>

      {/* Edge by category — where this wallet actually wins (and bleeds) */}
      <CategoryBreakdown rows={data.categoryBreakdown} />

      {/* Trade history charts */}
      <WalletHistory history={data.history} />

      {/* Audit trail — raw trades + reconstructed performance */}
      <WalletAudit address={data.address} />
    </div>
  );
};

const CompBar = ({ label, value = 0 }) => (
  <div className="flex items-center gap-3">
    <span className="label-mono text-[9px] text-retail w-48 shrink-0">{label}</span>
    <div className="flex-1 h-2 bg-surface2 rounded-sm overflow-hidden border border-hair">
      <div className="h-full bg-insider transition-all duration-700" style={{ width: `${Math.min(100, value ?? 0)}%` }} />
    </div>
    <span className="mono text-[11px] font-bold text-slate-200 w-8 text-right">{value == null ? "—" : Math.round(value)}</span>
  </div>
);

const StatCell = ({ label, value, color, sub }) => (
  <div className="panel p-3">
    <div className="label-mono text-[8px] text-retail mb-1">{label}</div>
    <div className="mono text-sm font-bold" style={{ color: color || "#f1f5f9" }}>
      {value ?? "—"}
    </div>
    {sub && <div className="label-mono text-[8px] text-retail mt-0.5">{sub}</div>}
  </div>
);

const RELIABILITY = {
  verified: { color: "#00E599", Icon: ShieldCheck },
  fair: { color: "#5B8DEF", Icon: ShieldCheck },
  partial: { color: "#F5A623", Icon: ShieldAlert },
  thin: { color: "#8892a0", Icon: ShieldQuestion },
};

const ReliabilityBadge = ({ reliability }) => {
  if (!reliability) return null;
  const cfg = RELIABILITY[reliability.tier] || RELIABILITY.thin;
  const Icon = cfg.Icon;
  return (
    <div
      data-testid="wallet-reliability-badge"
      title={reliability.note}
      className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 mt-1 cursor-help"
      style={{ borderColor: `${cfg.color}55`, backgroundColor: `${cfg.color}14` }}
    >
      <Icon size={12} style={{ color: cfg.color }} strokeWidth={2.5} />
      <span className="label-mono text-[9px]" style={{ color: cfg.color }}>
        {reliability.label}
      </span>
    </div>
  );
};

const barColor = (wr) => (wr >= 55 ? "#00E599" : wr >= 45 ? "#5B8DEF" : "#FF3B5C");

const CategoryBreakdown = ({ rows }) => {
  if (!rows || rows.length === 0) return null;
  const top = rows.slice(0, 12);
  return (
    <div className="panel p-5" data-testid="wallet-category-breakdown">
      <div className="flex items-center justify-between mb-3">
        <div className="label-mono text-[10px] text-slate-400">Measured returns by category</div>
        <span className="label-mono text-[8px] text-retail">
          winrate · W-L · net pnl
        </span>
      </div>
      <div className="space-y-2">
        {top.map((r) => {
          const wr = Math.round((r.winrate || 0) * 100);
          const pos = (r.netPnl || 0) >= 0;
          return (
            <div
              key={r.category}
              data-testid={`category-row-${r.category}`}
              className="flex items-center gap-3"
            >
              <span className="mono text-[11px] text-slate-200 w-32 shrink-0 truncate">
                {r.category}
              </span>
              <div className="flex-1 h-2 bg-surface2 rounded-sm overflow-hidden border border-hair min-w-[40px]">
                <div
                  className="h-full transition-all duration-700"
                  style={{ width: `${wr}%`, backgroundColor: barColor(wr) }}
                />
              </div>
              <span className="mono text-[10px] text-slate-300 w-9 text-right">{wr}%</span>
              <span className="mono text-[10px] text-fade w-10 text-right">
                {r.wins}-{r.losses}
              </span>
              <span
                className="mono text-[11px] font-bold w-20 text-right"
                style={{ color: pos ? "#00E599" : "#FF3B5C" }}
              >
                {fmtUsd(r.netPnl)}
              </span>
            </div>
          );
        })}
      </div>
      {rows.length > 12 && (
        <div className="label-mono text-[8px] text-retail mt-3 pt-2 border-t border-hair">
          + {rows.length - 12} more categories
        </div>
      )}
    </div>
  );
};
