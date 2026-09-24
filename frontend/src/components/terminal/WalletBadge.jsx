import React from "react";
import { walletMeta } from "@/lib/meta";

export const WalletBadge = ({ category, className = "", testId }) => {
  const m = walletMeta(category);
  const Icon = m.icon;
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 label-mono font-semibold text-[10px] px-1.5 py-0.5 rounded-sm border ${className}`}
      style={{
        color: m.color,
        backgroundColor: `${m.color}1f`,
        borderColor: `${m.color}55`,
      }}
    >
      <Icon size={11} strokeWidth={2.5} />
      {m.label}
    </span>
  );
};

export const SubTags = ({ tags = [] }) => {
  if (!tags?.length) return null;
  return (
    <span className="inline-flex gap-1">
      {tags.map((t) => {
        const m = walletMeta(t);
        return (
          <span
            key={t}
            className="label-mono text-[9px] px-1 py-0.5 rounded-sm border"
            style={{ color: m.color, borderColor: `${m.color}44` }}
          >
            +{m.label}
          </span>
        );
      })}
    </span>
  );
};
