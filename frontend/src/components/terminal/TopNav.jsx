import React from "react";
import { NavLink } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, ScanSearch, RefreshCw, SlidersHorizontal, Radio } from "lucide-react";

const links = [
  { to: "/", label: "MARKETS", icon: Activity, testId: "nav-item-markets", end: true },
  { to: "/tape", label: "TAPE", icon: Radio, testId: "nav-item-tape" },
  { to: "/wallet", label: "CLASSIFIER", icon: ScanSearch, testId: "nav-item-wallet-classifier" },
  { to: "/tune", label: "TUNE", icon: SlidersHorizontal, testId: "nav-item-tune" },
];

export const TopNav = () => {
  const qc = useQueryClient();
  return (
    <header className="border-b border-hair bg-canvas/90 backdrop-blur sticky top-0 z-30">
      <div className="max-w-[1600px] mx-auto px-4 h-14 flex items-center gap-4">
        <div data-testid="terminal-logo" className="flex items-center gap-2 shrink-0">
          <div className="leading-none">
            <div className="mono font-extrabold text-sm tracking-tight text-white">
              POWER<span className="text-brand">PLAY</span> INTEL
            </div>
            <div className="label-mono text-[8px] text-retail">SMART MONEY TERMINAL</div>
          </div>
        </div>

        <nav className="flex items-center gap-1 ml-2">
          {links.map((l) => {
            const Icon = l.icon;
            return (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.end}
                data-testid={l.testId}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 px-3 py-1.5 rounded-sm label-mono text-[10px] transition-colors ${
                    isActive
                      ? "bg-[#141a23] text-brand border border-[#A855F744]"
                      : "text-retail hover:text-slate-200 border border-transparent"
                  }`
                }
              >
                <Icon size={13} />
                <span className="hidden sm:inline">{l.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <button
          data-testid="terminal-refresh-btn"
          onClick={() => qc.invalidateQueries()}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-sm label-mono text-[10px] text-retail hover:text-brand border border-hair hover:border-[#A855F744] transition-colors"
        >
          <RefreshCw size={12} />
          <span className="hidden sm:inline">SYNC</span>
        </button>
      </div>
    </header>
  );
};
