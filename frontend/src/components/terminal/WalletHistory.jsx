import React from "react";
import {
  AreaChart,
  Area,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
  ResponsiveContainer,
} from "recharts";
import { TrendingUp, Crosshair } from "lucide-react";
import { fmtUsd } from "@/lib/format";

const GREEN = "#00E599";
const RED = "#FF3B5C";

const fmtDate = (t) =>
  new Date((Number(t) || 0) * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });

const EquityTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="panel-2 px-2.5 py-2 text-[10px] mono shadow-lg max-w-[240px]">
      <div className="text-slate-400 mb-0.5">{fmtDate(p.t)}</div>
      <div className="text-slate-200 truncate mb-1">{p.title}</div>
      <div className="flex justify-between gap-4">
        <span className="text-retail">Trade PnL</span>
        <span style={{ color: p.pnl >= 0 ? GREEN : RED }}>{fmtUsd(p.pnl)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-retail">Cumulative</span>
        <span style={{ color: p.cum >= 0 ? GREEN : RED }}>{fmtUsd(p.cum)}</span>
      </div>
    </div>
  );
};

const EntryTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="panel-2 px-2.5 py-2 text-[10px] mono shadow-lg max-w-[240px]">
      <div className="text-slate-200 truncate mb-1">{p.title}</div>
      <div className="flex justify-between gap-4">
        <span className="text-retail">Entry</span>
        <span className="text-insider">{Math.round(p.entry * 100)}¢</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-retail">Realized PnL</span>
        <span style={{ color: p.won ? GREEN : RED }}>{fmtUsd(p.pnl)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-retail">Invested</span>
        <span className="text-slate-300">{fmtUsd(p.invested)}</span>
      </div>
    </div>
  );
};

export const WalletHistory = ({ history }) => {
  const series = history?.pnlSeries || [];
  const entry = history?.entryTiming || [];
  if (!series.length && !entry.length) {
    return (
      <div className="panel p-8 text-center" data-testid="wallet-history-empty">
        <span className="label-mono text-[11px] text-retail">
          No resolved trade history to chart yet.
        </span>
      </div>
    );
  }

  const total = history?.totalRealized || 0;
  const curveColor = total >= 0 ? GREEN : RED;

  return (
    <div className="space-y-4" data-testid="wallet-history">
      <p className="text-xs text-slate-500">Chart dates use observed resolution bounds or last cashflow activity when exact resolution timing is unavailable.</p>
      {/* Equity curve */}
      <div className="panel p-5" data-testid="wallet-pnl-chart">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <TrendingUp size={13} className="text-sharp" />
            <span className="label-mono text-[10px] text-slate-400">
              Cumulative measured cashflow P&amp;L
            </span>
          </div>
          <span className="mono text-sm font-bold" style={{ color: curveColor }}>
            {fmtUsd(total)}
          </span>
        </div>
        {series.length > 1 ? (
          <ResponsiveContainer width="100%" height={210}>
            <AreaChart data={series} margin={{ top: 4, right: 6, left: -6, bottom: 0 }}>
              <defs>
                <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={curveColor} stopOpacity={0.32} />
                  <stop offset="100%" stopColor={curveColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2633" vertical={false} />
              <XAxis
                dataKey="t"
                tickFormatter={fmtDate}
                stroke="#475569"
                tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
                tickLine={false}
                axisLine={{ stroke: "#1e2633" }}
                minTickGap={44}
              />
              <YAxis
                tickFormatter={fmtUsd}
                stroke="#475569"
                tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
                tickLine={false}
                axisLine={false}
                width={50}
              />
              <Tooltip content={<EquityTooltip />} cursor={{ stroke: "#334155" }} />
              <ReferenceLine y={0} stroke="#334155" strokeDasharray="2 2" />
              <Area
                type="monotone"
                dataKey="cum"
                stroke={curveColor}
                strokeWidth={2}
                fill="url(#equityFill)"
                isAnimationActive
                animationDuration={700}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[120px] flex items-center justify-center label-mono text-[10px] text-retail">
            Not enough resolved trades to plot a curve.
          </div>
        )}
      </div>

      {/* Entry-timing scatter */}
      <div className="panel p-5" data-testid="wallet-entry-chart">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-1.5">
            <Crosshair size={13} className="text-insider" />
            <span className="label-mono text-[10px] text-slate-400">
              Entry Price &amp; Outcome
            </span>
          </div>
          <span className="label-mono text-[9px] text-retail">
            avg entry {Math.round((history?.avgEntry || 0) * 100)}¢ ·{" "}
            <span className="text-sharp">{history?.winners || 0}W</span>/
            <span className="text-fade">{history?.losers || 0}L</span>
          </span>
        </div>
        <p className="text-[11px] text-retail mb-3">
          Entry price versus observed cashflow P&amp;L; bubble size = capital. This chart alone does not establish a pricing advantage.
        </p>
        <ResponsiveContainer width="100%" height={230}>
          <ScatterChart margin={{ top: 6, right: 10, left: -6, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e2633" />
            <XAxis
              type="number"
              dataKey="entry"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={(c) => `${Math.round(c * 100)}¢`}
              stroke="#475569"
              tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
              tickLine={false}
              axisLine={{ stroke: "#1e2633" }}
              name="Entry"
            />
            <YAxis
              type="number"
              dataKey="pnl"
              tickFormatter={fmtUsd}
              stroke="#475569"
              tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
              tickLine={false}
              axisLine={false}
              width={50}
              name="PnL"
            />
            <ZAxis type="number" dataKey="invested" range={[40, 460]} name="Invested" />
            <ReferenceLine y={0} stroke="#334155" strokeDasharray="2 2" />
            <Tooltip content={<EntryTooltip />} cursor={{ strokeDasharray: "3 3", stroke: "#334155" }} />
            <Scatter data={entry} isAnimationActive animationDuration={600}>
              {entry.map((e, i) => (
                <Cell
                  key={i}
                  fill={e.won ? GREEN : RED}
                  fillOpacity={0.5}
                  stroke={e.won ? GREEN : RED}
                  strokeOpacity={0.9}
                />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
