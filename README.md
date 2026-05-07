# Hierag Monorepo

This repository follows a `core + interfaces` layout.

## Structure

- `core/`: shared Python logic, database access, retrieval pipeline, and models.
- `interfaces/api/`: FastAPI interface that serves chat/debug endpoints.
- `interfaces/client/`: Vite + React client and Storybook stories.
- `interfaces/dashboard/`: Streamlit-oriented placeholder interface.
- `interfaces/web/`: FastHTML-oriented placeholder interface.
- `docs/`: Quarto documentation and tutorials.
- `data/`: SQLite runtime data files (gitignored).

## API

```bash
uv sync
uv run uvicorn interfaces.api.main:app --reload --port 8510
```

Useful env vars:

- `HIERAG_ADMIN_LOGIN_CODES`: comma-separated admin 4+2s.
- `HIERAG_PILOT_LOGIN_CODES`: comma-separated 4+2s that make up the pilot group used by the admin stats "Pilot group only" filter.

Run tests:

```bash
uv run pytest interfaces/api/tests
```

## Client

```bash
cd interfaces/client
npm install
npm run dev
```

Run Storybook:

```bash
cd interfaces/client
npm run storybook
```

## Documentation (Quarto)

Render:

```bash
cd docs
quarto render
```

Open:

- `http://localhost:8510/connections/reference`
- `http://localhost:8510/connections/reference/hybrid-retrieval`
- `http://localhost:8510/connections/reference/core-directory-diagram`
- `http://localhost:8510/connections/reference/tutorials/01_analysis`

Per-page URL pattern:

- `http://localhost:8510/connections/reference/<doc-slug>`

## Source Test Sets

Admins can create isolated source-test sandboxes from the client under `Source tests`. Each sandbox copies only `data/scraper.db`, not chat/user data, so admins can add/remove URLs, queue a sandbox refresh, and compare live vs sandbox answers without changing live answers. Sandbox files default to `data/source_sandboxes/`; override with `HIERAG_SOURCE_SANDBOX_DIR`.

Source test sets are sandbox-only. Live source changes should come from the nightly refresh process.

## Nightly Connections Refresh

Run live refresh manually:

```bash
uv run python -m core.daily_connections_refresh --site-id 2 --max-pages 3000 --prune-missing --prune-missing-after 1 --prune-status-codes 404,410
```

Refresh active source-test sandbox databases manually:

```bash
uv run python -m core.refresh_source_proposals --site-id 2 --max-pages 3000
```

Refresh only sandboxes explicitly queued from the admin UI:

```bash
uv run python -m core.refresh_source_proposals --queued-only --site-id 2 --max-pages 3000
```

Windows scheduler script:

- `scripts/run-nightly-connections-refresh.bat` (primary)
- `scripts/run-nightly-connections-refresh.cmd` (wrapper to `.bat`)
- Optional shared Task Scheduler target:
  - `C:\Users\harvgs-admin\Documents\Python Scripts\hierag_connections_refresh.bat`

Nightly logging outputs:

- Full run log: `data/logs/connections-refresh_<YYYY-MM-DD>_<HHMM>.log`

Nightly snapshot behavior:

- Before each refresh run, snapshots are attempted for `data/app_runtime.db` and `data/scraper.db`.
- Snapshot directory: `data/snapshots/nightly/`
- Snapshot filename format: `<db_stem>-YYYYMMDD-HHMMSS.db`
- Retention: snapshots older than 14 days are removed.
- Snapshot failures are fail-open: warnings are logged and refresh continues.

Operational note:

- After first deployment of stale-cache hardening, do a one-time API process restart after cleanup to clear any preloaded retrieval cache state.

## Verification Pattern

- Python files in `core/` include `# %%` script checks.
- React components include sibling `.stories.tsx` files with default and edge-case states.
