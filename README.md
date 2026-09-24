# PowerPlay Intel

A React/FastAPI dashboard for Polymarket sports and esports markets, wallet profiles, exposure, trade tape, audits, and configurable classification thresholds.

Market profiling now shows cached and incremental results, prioritizes opened markets, and distinguishes queued, running and not-started work. See [PROFILING.md](PROFILING.md) for caching, progress and the non-blocking market-detail API.

This edition implements the sports track-record Sharp model, rule **sports-record-2026-09-22.1**, with wallet and market-analysis schema **23**. See [SHARP_MODEL.md](SHARP_MODEL.md) for the rubric, data limitations and validation workflow. Historical reports and FIXES.md describe earlier versions.

## Requirements

- Python **3.12** (verified with 3.12.14).
- Node.js **24** (verified with 24.19.0), Yarn **1.22.22**.
- A running MongoDB instance, local or hosted. The database is not included.

## Configure

Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to `frontend/.env`. Both templates are included.

PowerShell:

```powershell
Copy-Item backend/.env.example backend/.env
Copy-Item frontend/.env.example frontend/.env
```

macOS/Linux:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Set `MONGO_URL`, `DB_NAME`, and `CORS_ORIGINS` in the backend environment. Defaults point to local MongoDB and the frontend at `http://localhost:3000`.

Generate an admin token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put it in `ADMIN_TOKEN`. The earlier export contained an admin credential in tests and reports; those copies have been redacted. If that credential remains active on a deployment, replace its environment value and restart that deployment. No live credential was changed by this repair.

## Run the backend

```bash
cd backend
python -m venv .venv
# PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8001
```

Use **one backend worker**: queues, rate limits, and tuning state are process-local. For public deployment, use TLS at a reverse proxy and exact frontend origins in `CORS_ORIGINS`. Set Uvicorn's `--forwarded-allow-ips` to only the proxy addresses you control. The app does not trust raw forwarded headers itself.

Admin routes return HTTP 503 if `ADMIN_TOKEN` is not configured. With admin controls configured, missing or invalid credentials return HTTP 401.

## Run the frontend

```bash
cd frontend
yarn install --frozen-lockfile
yarn start
```

Set `REACT_APP_BACKEND_URL=http://localhost:8001` for local development. If omitted, the app uses same-origin `/api`; hosting must route that path to the backend.

Run `yarn build` for production. The original CRA/CRACO toolchain remains. The build passed with the supplied lockfile; some dependencies emit deprecation and peer-dependency warnings.

## Optional AI narratives

The default is a local narrative requiring no AI package or key. To enable the provider integration, install `backend/requirements-ai.txt`, then set `EMERGENT_LLM_KEY`, `LLM_PROVIDER`, and a valid `LLM_MODEL` available to your account. Missing configuration, import failure, or provider errors use the local fallback. Provider-backed generation was not exercised in verification.

## Tests

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest
```

Default tests use deterministic records, mocked HTTP responses, and an in-memory MongoDB substitute. No services or keys are needed. Public API smoke checks skip unless `POWERPLAY_TEST_URL` explicitly identifies a backend to test. They send no admin credentials and do not mutate configuration.

Old version-specific tests are retained as non-executable text in `test_reports/legacy_tests`. Active accounting, failure-path, security, cache, and API regressions are in `backend/tests`.

## Accounting and coverage

- Outcome tokens are tracked separately before aggregating market cashflow.
- Exposure uses current balances among sampled top holders. Recent trades belong to the tape and are never added to holdings.
- Performance includes markets whose tracked outcomes have all closed or are explicitly redeemable with usable payout values. Near-zero/one prices and missing position rows do not establish resolution.
- Partial exits from open markets enter **settled-market** metrics only when the whole market position closes. Fees/gas absent from API cashflows are not estimated.
- Plain binary SPLIT/MERGE cashflows are supported. Unsupported conversions, ambiguous redemptions, missing basis, and mismatched balances are flagged and excluded instead of guessed.
- Activity and positions are each paginated to a maximum of 5,000 rows. Position requests include fractional balances and archived markets. Valid partial measurements remain visible. Unknown history affects qualification separately; only qualified Sharps have numeric ranks. There is no fallback score of 50 or Insider label.
- Upstream errors return HTTP 502. “API ledger reconciled” describes fetched API records, not an independent chain audit.
- Threshold changes invalidate market analyses. Configuration signatures and schema versions prevent obsolete cached calculations from being reused.

API assumptions were checked against the [activity documentation](https://docs.polymarket.com/api-reference/core/get-user-activity), [positions documentation](https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user), and [holders documentation](https://docs.polymarket.com/api-reference/core/get-top-holders-for-markets). This repair retains the existing v1 endpoints; migrating to v2 is a separate change.

## Admin controls

Enter your token on the Tune page or use `Authorization: Bearer <ADMIN_TOKEN>` with:

- `PUT /api/config/thresholds`
- `POST /api/config/thresholds/reset`
- `POST /api/refresh`
- `GET /api/admin/heal`
- `POST /api/admin/heal`

The original source ZIP is unchanged. No live deployment or external account was modified.

