# Hierag API Interface

FastAPI entrypoint lives in `main.py` and imports shared logic from `core/`.

Runtime SQLite files are stored under `data/`:

- `data/app_runtime.db` for app data
- `data/scraper.db` for scraper pipeline data
