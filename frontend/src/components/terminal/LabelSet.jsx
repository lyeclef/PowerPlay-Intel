import React from "react";
import { WalletBadge } from "./WalletBadge";

// Renders the full cohesive label set (e.g. SHARP + WHALE, or BOT + MARKET_MAKER).
export const LabelSet = ({ labels = [], category, className = "", testId }) => {
  const list = labels && labels.length ? labels : category ? [category] : [];
  return (
    <span data-testid={testId} className={`inline-flex flex-wrap items-center gap-1 ${className}`}>
      {list.map((l) => (
        <WalletBadge key={l} category={l} />
      ))}
    </span>
  );
};
