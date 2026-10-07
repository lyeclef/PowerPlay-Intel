# PowerPlay Intel — all six signal corrections

Prepared locally on 7 October 2026 from the user's VPS bundle. This includes the earlier sports-scope and tape corrections plus the remaining entry-price, cohort and pagination repairs.

## Behavior

1. **Accepted cashouts:** selling at 97 cents or higher counts toward the holding policy. Sales below that threshold do not gain retention credit from current closed flags, elapsed game time or profit-capture heuristics. Original-share and original-capital requirements remain 90%, with hedged shares excluded. Audit records distinguish credited cashouts from physical retention; wallet evidence shows both. Pending markets still require observed resolution before entering the resolved holding cohort.
2. **Entries:** unknown or invalid historical entries stay unknown. Current price is never substituted for entry. Observed acquisition prices are stored separately for YES and NO. Unknown comparisons display no slippage or favorable tail label. Mixed entry coverage is disclosed and does not produce a group-wide price-comparison label. Slippage fields consistently use cents.
3. **Sports qualification:** unrelated all-market losses do not erase a sufficient sports track record. A certified sport specialty can qualify a wallet even when its aggregate sports record is weaker. Market eligibility remains sport-specific, and automation/holding exclusions still apply.
4. **Combined signals:** Sharps, Candidates and Underdogs contribute consistently to strength, counts, capital and picks. Existing weights are retained: Sharp score / 100, Candidate 0.50, Underdog 0.35. Candidates and Underdogs have separate counts and UI labels. Sharp-only mode cannot fall back to a combined-only pick. Wallet-level excluded styles cannot contribute via a residual candidate/underdog scope. Picks share one calculation between fresh and saved results.
5. **Activity history:** requests use inclusive timestamp windows with a boundary offset, preserving fills made in the same second across page boundaries. Requests explicitly use TIMESTAMP/DESC order. Window/offset limits and page budgets return a partial-sample flag rather than falsely claiming complete history. Invalid timestamps or ignored ordering/window constraints fail as upstream errors.
6. **Tape:** Sharp flags, labels and scores use the same sport qualification check as market analysis. Separate wallet-level identity fields are retained for context. A specialist does not drive unrelated ineligible sports markets.

Saved market analyses revalue observed holder quantities using the available market snapshot. Picks, strength and price comparisons update together; underlying balances and qualification remain observations from the original profile timestamps. Refreshing prices does not claim that holders were refetched. Displayed market lists/search results now queue all selected events within the bounded job capacity; opening a market retains foreground priority.

The pagination design was checked against [Polymarket's activity API documentation](https://docs.polymarket.com/api-reference/core/get-user-activity), which documents stable ordering, timestamp windows and a per-window offset budget.

## Versioning and project contents

- Rule version: `sports-signals-2026-10-07.2`.
- Wallet schema: 25. Market analysis schema: 24.
- Changed signatures/schema exclude incompatible cached results and separate validation baselines. Existing backend startup healing recomputes incompatible profiles.
- Backend source, regression tests, compiled frontend and editable frontend application source are included.
- Active frontend source and CSS were recovered from the supplied bundle's source maps. Build configuration and the dependency manifest/lockfile were restored from the previous local project and verified with a successful production build; they are not an export of the original VPS frontend build configuration.
- `.env.example` templates are provided. Runtime `.env` credentials, installed dependencies and local cache/runtime files are excluded.

## Verification

- **172 backend tests passed**, with three opt-in deployed-API smoke checks skipped because no deployment test URL was configured.
- The earlier worker-count tests were adapted to the VPS bundle's existing one-background/one-foreground design. An older timestamp-free pagination fixture now supplies valid timestamps. These adaptations do not weaken the original assertions about worker cleanup, priority scheduling or explicit sampling limits.
- **Six frontend render checks passed**, covering unknown/partial entries, separate underdog counts and Sharp-only isolation.
- **Production frontend build passed.**
- A read-only live API check compared four five-record window pages with one continuous twenty-record page at the same frozen timestamp. The records matched exactly. Synthetic tests separately cover large groups of tied timestamps.
- These checks do not establish production VPS health or prediction accuracy on future bets.

Run backend checks from `backend` with `python -m pytest tests -q` after installing development requirements. Run frontend render checks with `yarn test:render` and build with `yarn build` after installing the pinned frontend dependencies.

## Updating an existing VPS

Update the backend source files and the frontend `build` directory from this package. Preserve the deployment's `.env`, database, settings and virtual environment. Restart the backend through its normal service mechanism so new rule signatures take effect. The frontend assets in this ZIP are already rebuilt.

This is a local release artifact; no VPS deployment or GitHub push was performed.
