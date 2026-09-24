# Sports track-record qualification and ranking

Rule `sports-record-2026-09-22.1`; wallet and analysis schema 23.

A Sharp is a bettor with a sufficient winning, profitable sports record who meets the required holding style and automation screen. Price quality, closing-line value, fee completeness, pre-match timing, confidence intervals and future validation do not determine qualification or rank.

## Default requirements

| Requirement | Default |
|---|---|
| Resolved sports sample | At least 100 distinct underlying events |
| Track-record duration | At least 60 days between the first and last entries in the sample |
| Event win rate | At least 55% profitable events |
| ROI | Strictly positive observed P&L divided by invested capital |
| Positions held | At least 90% of measured resolved positions retain at least 90% of originally acquired directional shares |
| Capital held | At least 90% of original entry-cost capital retained through resolution |
| Recent holding | The same position and capital checks on the latest 50 resolved events |
| Usable observed records | At least 80% event coverage and holding coverage |
| Automation | Low observed automation risk; substantial paired-inventory styles excluded |

The default performance window is 180 days, extended to 365 if needed for the event count or Proven tier. Sports-wide holding is also checked over the extended window. These are configurable product thresholds, not an established universal definition or a claim about future profits.

Sports and esports are combined into an overall Sports scope. Live bets count. Non-sports markets and weather do not affect sports qualification. A qualifying overall record can contribute to signals across recognized sports. A sport-specific qualifying record contributes only in that sport. The leaderboard and wallet detail identify the qualifying scope and show its metrics; all-market cashflows are separately labelled.

Proven Sharp uses the same performance and holding requirements with at least 300 events spanning 180 days. Future observations are informational and do not block this tier.

## Performance and holding

Related positions are netted by underlying event before counting a win, loss or sample. A win means the event's combined measured P&L is positive. Break-even events are not wins. ROI uses total observed P&L divided by total invested capital; it does not average individual-market percentages. Rewards and rebates are separate from directional returns. Missing fee records do not disqualify a wallet; only reported fees enter observed cashflows.

Losses are included. Pending positions stay pending and void/refund outcomes are excluded. Unknown event grouping, missing acquisition basis or unreconciled cashflows cannot create a measured win or a usable event. A capped API history can qualify as a labelled sample if it contains enough usable events; an unknown full history does not automatically reject it.

FIFO lots retain original entry cost. Early sales reduce continuous retention, and buying back creates another acquisition lot. Opposing outcome purchases cancel matched directional continuity. Keeping dust does not count as holding the original position. Unattributed transfers or unsupported conversions remain uncertain.

The displayed holding percentages use measured positions and capital. Coverage reports how much of the observed cohort could be measured. Worst-case bounds are diagnostic and may be unavailable for a capped feed. Reported close/observed resolution dates are timing proxies, not claims of exact oracle settlement times. Cached regrades preserve the original source snapshot date and do not represent fresh upstream data.

## Classification and bots

Categories are Proven Sharp, Sharp, Candidate, Conviction Holder, Active Trader, Probable Bot, Automation Uncertain, Hedged Style and Insufficient Data. Whale is only a size tag.

Candidates have positive measured returns, the required holding style and at least 30 measured events but miss one or more Sharp requirements. Conviction Holders meet holding checks without a sufficient qualifying record. Active Traders demonstrably fail the holding style. All categories retain measured performance; only qualified Sharps receive a numeric rank.

Bot screening combines timing, two-sided turnover/inventory and repeated cross-market coordination. Two strong signal groups imply Probable Bot; one means Automation Uncertain. Both are excluded from Sharp ranking. Partial fills are grouped into estimated execution episodes. Volume, rewards, overnight activity and profitability alone do not establish automation. Low observed risk is not verified human identity; public records do not reveal every hidden hedge or private order cancellation.

## Ranking formula

Only qualified Sharps receive the 0-100 rank. It is a weighted index, not a predicted win probability or a percentile.

| Component | Weight | Scaling |
|---|---:|---|
| Win rate | 40% | Event win rate times 100 |
| ROI | 40% | 50 + ROI times 200, capped at 100; e.g. 10% ROI gives 70 component points |
| Record depth | 20% | 50 times events/300 + 50 times elapsed days/180, capped at 100 |

The final weighted result is rounded to one decimal. The ROI component's baseline is not a fallback wallet score. Ineligible wallets show no numeric Sharp rank, while their observed WR, ROI and category remain visible.

## Market signals

A wallet contributes only when its overall Sports record or this market's sport qualifies. Live entries are included. Observed matched YES/NO shares are removed before calculating directional capital. Each side's signal is its share of score-weighted qualified capital among sampled top holders. This is a capital distribution, not an outcome probability.

A market may still have no qualifying participants in the fetched sample. That produces an unavailable signal, not an invented 50/50. Wallet fetch failures and limited holder coverage remain visible.

## Validation and local operation

Tests cover WR and ROI gates, insufficient samples, 90% holding, retained losses, opposing positions, automation, cross-sport qualification, specialist scope display, and eligibility with missing/negative CLV, unknown fees and live timing. Optional frozen-baseline tracking and historical comparison remain available but do not affect eligibility. Existing quote diagnostics are not score inputs.

Use the README for a new installation. In the current local workspace, `outputs/Start-PowerPlay.ps1` starts this source at http://localhost:3000 with backend http://127.0.0.1:8001 and database `powerplay_sharp_local`. `outputs/Stop-PowerPlay.ps1` stops these local services. No GitHub push is part of this update.
