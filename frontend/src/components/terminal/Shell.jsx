import React from "react";
import { Outlet } from "react-router-dom";
import { TopNav } from "./TopNav";
import { TickerBar } from "./TickerBar";

export const Shell = () => (
  <div className="min-h-screen bg-canvas text-slate-200">
    <TopNav />
    <TickerBar />
    <main className="terminal-grid-bg">
      <div className="max-w-[1600px] mx-auto px-4 py-5 min-h-[calc(100vh-9rem)]">
        <Outlet />
      </div>
    </main>
  </div>
);
