# Progressive market profiling

Market detail requests now return available results and job status immediately. The UI polls active jobs every 1.5 seconds and updates the wallet table as profiles finish. Market and search lists refresh every 5 seconds. Sharp qualification, score weights and history sampling limits are unchanged.

## Scheduling

Every displayed event's selected market is queued. Other submarkets are marked `not_started` until selected. Two background workers handle the list, and a separate foreground worker is reserved for opened markets. Opening a waiting market promotes that same job; duplicate requests do not start duplicate market analyses. Already-running jobs continue. The queue is bounded, with opened markets taking precedence over waiting background work.

`profiling.state` is `not_started`, `waiting`, `profiling`, `ready` or `error`. Progress includes total, checked, available, pending, failed and cached wallet counts. A ready result can still contain unavailable wallets; these are explicitly reported and retried on a shorter cache interval. Errors retain any available results and expose a retry action.

## Cache and partial results

- Completed market analyses normally remain fresh for 30 minutes. Previously saved compatible analyses stay visible while refreshing, with their timestamp shown.
- Wallet profiles less than one hour old are reused. Compatible profiles up to 24 hours old can be shown while refreshing; these are labelled saved data. Older or incompatible-rule profiles are excluded from previews.
- Partial results are published in memory as wallets finish. Pending wallets are not counted as failures. Only the final batch result is persisted, and an interrupted refresh does not overwrite the last saved market analysis.
- Final results with failed wallet lookups or saved profiles use a 60-second refresh interval instead of the normal 30-minute lifetime.
- A partial signal can change as additional wallets finish. The UI identifies partial results and preserves current sample-coverage information.
- A rubric change cancels market jobs and starts a separate evaluation. Obsolete results cannot appear under the new configuration signature.

## Historical market metadata

Overlapping condition IDs share one fetch. Unrelated wallets can fetch metadata concurrently; the cache lock no longer encloses external requests. Cancelling one waiter does not cancel a fetch another wallet needs. Shutdown explicitly cancels outstanding tasks.

Resolved market metadata is reused for 24 hours; unresolved metadata for five minutes. Failed refreshes preserve previously observed resolution data and briefly back off. The original first-observed resolution timestamp is retained. No closing-price or fee qualification requirement was added.

## API contract

`GET /api/markets/{condition_id}` returns `{market, analysis, profiling}`. `analysis` may be null while a cold job waits or fetches holders. Poll while `profiling.state` is `waiting` or `profiling`. `analysis.isPartial` and `analysis.coverageDetail` describe incomplete coverage. `GET /api/markets/{condition_id}?retry=true` explicitly retries a failed analysis. Each listed market also includes `profiling` status.

Wallet tasks retain the existing 75-second timeout. The full market job has a 150-second bound. Initial profiling still requires public API history; the improvement is that usable results arrive progressively and opened markets do not wait behind the whole background list.

Regression tests cover queue promotion, bounded scheduling, all displayed markets, prompt cached responses, incremental wallet results, old-rule and expired-cache exclusion, request sharing, cancellation and retryable failures.
