import axios from "axios";

const BASE =
  process.env.NODE_ENV === "production"
    ? "/api"
    : `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;

export const api = axios.create({ baseURL: BASE, timeout: 60000 });

export const fetchCategories = () =>
  api.get("/categories").then((r) => r.data.categories);

export const fetchMarketTypes = () =>
  api.get("/market-types").then((r) => r.data.types);

export const fetchMarkets = (category = "esports", type = null, limit = 30) =>
  api
    .get("/markets", { params: { category, type: type || undefined, limit } })
    .then((r) => r.data);

export const searchMarkets = (q, limit = 20) =>
  api.get("/search", { params: { q, limit } }).then((r) => r.data);

export const fetchMarketDetail = (id, retry = false) =>
  api.get(`/markets/${id}`, { params: { retry }, timeout: 15000 }).then((r) => r.data);

export const fetchWallet = (address) =>
  api.get(`/wallets/${address}`, { timeout: 180000 }).then((r) => r.data);

export const fetchLeaderboard = (limit = 60, view = "sharp") =>
  api.get("/leaderboard", { params: { limit, view } }).then((r) => r.data);

export const fetchStats = () => api.get("/stats").then((r) => r.data);

export const fetchThresholds = () =>
  api.get("/config/thresholds").then((r) => r.data);

export const fetchWalletAudit = (address, limit = 60) =>
  api.get(`/wallets/${address}/audit`, { params: { limit }, timeout: 180000 }).then((r) => r.data);

const adminHeader = (token) => (token ? { Authorization: `Bearer ${token}` } : {});

export const saveThresholds = (config, token) =>
  api
    .put("/config/thresholds", { config }, { headers: adminHeader(token) })
    .then((r) => r.data);

export const resetThresholds = (token) =>
  api
    .post("/config/thresholds/reset", null, { headers: adminHeader(token) })
    .then((r) => r.data);

export const fetchTape = (sharpOnly = false, minSize = 0, limit = 80) =>
  api
    .get("/tape", { params: { sharp_only: sharpOnly, min_size: minSize, limit } })
    .then((r) => r.data);

// pick the market to display for an event given the active type filter
export const pickDisplayMarket = (event, marketType) => {
  const markets = event?.markets || [];
  if (!markets.length) return null;
  if (marketType) {
    const match = markets.find((m) => m.marketType === marketType);
    if (match) return match;
  }
  return markets[0];
};

export const MARKET_TYPE_META = {
  moneyline: { label: "Moneyline", short: "ML" },
  spread: { label: "Spread", short: "SPRD" },
  total: { label: "Totals", short: "O/U" },
  prop: { label: "Props", short: "PROP" },
};

export const fetchValidation = () => api.get("/validation").then(r => r.data);
