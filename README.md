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
poetry install
poetry run uvicorn interfaces.api.main:app --reload --port 8510
```

Run tests:

```bash
poetry run pytest interfaces/api/tests
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

## Verification Pattern

- Python files in `core/` include `# %%` script checks.
- React components include sibling `.stories.tsx` files with default and edge-case states.
