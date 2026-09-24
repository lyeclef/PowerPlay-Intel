# Repair record

## Correctness

- Added outcome-level cashflow accounting and balance reconciliation in `backend/performance.py`.
- Fixed order-dependent profit when holding both outcomes: the $0 example remains $0 in either input order.
- Removed trade-volume additions from holder exposure, including sells and double-counted purchases.
- Replaced price-threshold resolution guesses with explicit redeemability and balance checks.
- Covered zero-value redemptions, fractional holdings, partial exits, missing basis, and binary splits/merges.
- Capped or ambiguous history is disclosed and excluded from trusted performance. A cap alone no longer labels a wallet a bot.

## Reliability and security

- Failed or malformed API responses cannot end pagination as successful empty pages.
- Added position pagination, fractional/archived-position coverage, and the documented 20-holder limit.
- Added schema and configuration-signature cache invalidation.
- Validated finite/nonnegative weights, ratios, count thresholds, and consistent bounds.
- Redacted the exported admin credential from historical files. No live credential was tested or changed.
- Prevented raw `X-Forwarded-For` from bypassing rate limits; bounded limiter state.
- Updated runtime dependencies; supplied exact backend dependency locks and `frontend/yarn.lock`.
- Added background-task tracking, cancellation, and lifespan shutdown.

## Interface and setup

- Added retryable errors to markets, leaderboard, tape, market detail, audit, and tuning.
- Partial wallet/audit data no longer displays win rate and ROI as reliable. Score labels reflect configured weights.
- Added both environment templates and rewrote setup instructions.
- Made AI dependencies optional and provider/model configuration explicit.
- Archived old live-wallet/version-specific assertions and replaced them with deterministic regressions.

## Verification

- Backend: **68 passed, 3 skipped**. Skips are opt-in public-service smoke checks.
- Frontend production build passed with `CI=true`, Node 24.19.0, and Yarn 1.22.22.
- Python verification used 3.12.14. Exact dependency locks record the installed package set. A fresh install from the backend lock passed the full default suite; Yarn frozen-lockfile validation also passed.
- API tests used mocked HTTP and an in-memory MongoDB substitute. A real MongoDB-backed deployment, browser interaction tests, and paid AI calls were not run.

## Deployment

Follow `README.md` and configure MongoDB plus a new admin token. If the earlier embedded credential is still active, rotate it on that deployment. Unsupported or ambiguous activity deliberately remains labeled partial rather than treated as proven performance.
