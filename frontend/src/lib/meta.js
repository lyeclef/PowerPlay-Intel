import { Crosshair, Waves, ShieldCheck, ShieldQuestion, Scale, Bot, Timer, User, Search } from "lucide-react";
export const WALLET_META = {
  PROVEN_SHARP: {label: "ELITE", color: "#00D2FF", icon: ShieldCheck, blurb: "Meets Sharp requirements with at least 300 resolved sports events across 180 days."},
  SHARP: {label: "SHARP", color: "#00E599", icon: Crosshair, blurb: "Sufficient sports track record with qualifying win rate, positive ROI, 90% holding and no automation exclusion."},
  CANDIDATE: {label: "CANDIDATE", color: "#A7C4FF", icon: Search, blurb: "Promising holding and measured returns; building sample toward certified Sharp qualification."},
  RETAIL: {label: "CASUAL", color: "#64748B", icon: User, blurb: "Recreational or uncertified participant without verified statistical edge."},
  ACTIVE_TRADER: {label: "SCALPER", color: "#FB7185", icon: Timer, blurb: "Exits or hedges before resolution. Trading odds movements rather than holding sports outcomes."},
  PROBABLE_BOT: {label: "BOT", color: "#94A3B8", icon: Bot, blurb: "Multiple observed behavior groups confirm automation; excluded from Sharp ranking."},
  AUTOMATION_UNCERTAIN: {label: "AUTOMATION UNCERTAIN", color: "#FBBF24", icon: ShieldQuestion, blurb: "One strong behavior group needs review; public activity does not establish who operates the wallet."},
  HEDGED_STYLE: {label: "MAKER", color: "#F59E0B", icon: Scale, blurb: "Substantial two-sided inventory resembles market making or spread capture, rather than directional betting."},
  INSUFFICIENT_DATA: {label: "UNRANKED", color: "#94A3B8", icon: User, blurb: "Not enough usable evidence to classify or rank. Available measurements are preserved."},
  WHALE: {label: "WHALE", color: "#A855F7", icon: Waves, blurb: "Large observed size; this tag does not imply skill."},
};

WALLET_META.ELITE = WALLET_META.PROVEN_SHARP;
WALLET_META.SCALPER = WALLET_META.ACTIVE_TRADER;
WALLET_META.MAKER = WALLET_META.HEDGED_STYLE;
WALLET_META.CASUAL = WALLET_META.RETAIL;
WALLET_META.BOT = WALLET_META.PROBABLE_BOT;
WALLET_META.UNRANKED = WALLET_META.INSUFFICIENT_DATA;
WALLET_META.CONVICTION_HOLDER = WALLET_META.RETAIL;

export const walletMeta = cat =>
  WALLET_META[cat] ||
  (cat ? WALLET_META[cat.toUpperCase()] : null) ||
  Object.values(WALLET_META).find(m => m.label.toUpperCase() === (cat || "").toUpperCase()) ||
  WALLET_META.INSUFFICIENT_DATA;

