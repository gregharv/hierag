# Hierag Monorepo

This repository is organized as a monorepo with a FastAPI/FastLite backend and a Vite/React frontend.

## Structure

- `backend/app/`: FastAPI app, FastLite schema contracts, and business logic
- `backend/tests/`: pytest API tests
- `backend/playground/`: notebook exploration only
- `frontend/src/components/`: React UI components with Storybook stories
- `frontend/src/hooks/`: reusable frontend hooks
- `data/app_runtime.db`: primary SQLite app-data file for users/chats/messages (gitignored)
- `data/scraper.db`: scraper pipeline SQLite file (gitignored)

## Backend

```bash
cd backend
poetry install
poetry run uvicorn app.main:app --reload
```

Run tests:

```bash
cd backend
poetry run pytest
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Run Storybook:

```bash
cd frontend
npm run storybook
```

## Verification Pattern

- Backend python files include `# %%` script checks.
- Frontend components include sibling `.stories.tsx` files with default and edge-case states.

## Hybrid Retrieval Doc (Quarto)

Render the guide with Quarto (right-side clickable table of contents):

```bash
cd docs
quarto render hybrid-retrieval.qmd --to html --output hybrid-retrieval.html
```

Then open:

- `docs/hybrid-retrieval.html`
- or `http://localhost:8510/api/debug/hybrid-retrieval-doc` (backend now serves the Quarto page when available, and falls back to the built-in renderer if Quarto cannot run)
