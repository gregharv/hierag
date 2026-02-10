# AGENTS.md

> **Role:** You are an expert Full-Stack AI Developer specializing in "AI-Native" workflows.
> **Goal:** We prioritize **standard file formats** (.py, .ts, .tsx) over notebooks for logic, but we insist on **immediate interactivity** and verification via script markers, Storybook, and Quarto.

---

## 1. Project Structure (The "Core + Interfaces" Pattern)

We use a Monorepo structure where pure logic (`core`) is separated from delivery mechanisms (`interfaces`).

```text
my-project/
├── core/                    # SHARED PYTHON LOGIC (The "Brain")
│   ├── __init__.py
│   ├── logic.py             # Pure business logic (No API/UI code)
│   ├── models.py            # Pydantic/SQLModel schemas
│   └── db.py                # FastLite connection setup
│
├── interfaces/              # DELIVERY MECHANISMS (The "Heads")
│   ├── dashboard/           # Streamlit (Option A: Simple Data Apps)
│   │   └── app.py
│   ├── web/                 # FastHTML (Option B: Multi-Page Python Web App)
│   │   └── main.py
│   ├── client/              # Vite + React (Option C: Complex Client Interaction)
│   │   └── src/
│   └── api/                 # FastAPI (Needed only if using Option C)
│       └── main.py
│
├── data/                    # PERSISTENCE (Git-Ignored)
│   ├── app.db               # Main application database
│   ├── analytics.db         # Separate analytics events
│   ├── cache.db             # Temporary storage
│   └── uploads/             # User uploaded files
│
├── docs/                    # KNOWLEDGE BASE (Quarto)
│   ├── _quarto.yml          # Documentation config
│   ├── index.qmd            # Homepage
│   └── tutorials/           # Jupyter Notebooks for analysis
│       └── 01_analysis.ipynb
│
├── AGENTS.md                # This file (AI Instructions)
├── pyproject.toml           # Python dependencies (managed by uv)
├── uv.lock                  # Lockfile
└── README.md

```

---

## 2. Directory Boundaries & Rules

### A. `/core` (The Brain)

* **Content:** PURE Python logic, database access, and models.
* **Rule:** NEVER import from `interfaces/`. This module must be standalone.
* **Database:** Use `FastLite` for direct SQLite interaction.

### B. `/interfaces` (The "Decision Menu")

When starting a frontend task, choose the simplest option that fits the requirement:

* **Option A: Streamlit (`interfaces/dashboard`)**
* *Use when:* Building internal tools, admin panels, or quick data visualizations.
* *Stack:* Pure Python. Imports `core` directly.


* **Option B: FastHTML (`interfaces/web`)**
* *Use when:* Building a public multi-page website, a blog, or a standard CRUD app.
* *Stack:* Python + HTMX. Imports `core` directly. Server-Side Rendering.


* **Option C: Vite + React (`interfaces/client`)**
* *Use when:* Building a highly interactive "app-like" experience (drag-and-drop, complex state).
* *Stack:* TypeScript/React. REQUIRES `interfaces/api` (FastAPI) to talk to `core`.



### C. `/data` (The Persistence Layer)

* **Rule:** This folder is **Git-Ignored**.
* **Usage:**
* **Multiple DBs:** The system is designed to handle multiple SQLite files.
* **Naming:** `users.db`, `logs.db`, `jobs.db`.
* **Connection:** `db = database("data/users.db")`



---

## 3. Core Philosophy: "Verification First"

**Rule #1:** Never write code without immediately writing a way to verify it.

### Backend (Python)

* **Interactive Checks:** Every `.py` file in `core/` MUST end with a `if __name__ == "__main__":` block.
* **Marker:** Use the standard `# %%` cell marker.
* **Behavior:** The block must run a sanity check (e.g., create a temporary FastLite DB, insert a row, print the result).

**Example:**

```python
# core/users.py
from fastlite import database
def create_user(db, name):
    return db.t.users.insert(name=name)

# %%
if __name__ == "__main__":
    print("--- Verifying User Creation ---")
    db = database(":memory:")
    user = create_user(db, "Alice")
    print(f"User created: {user}")
    assert user['name'] == "Alice"
    print("Check Passed ✅")

```

### Frontend (React/Vite Only)

* **Storybook:** Every component (e.g., `Card.tsx`) MUST have a sibling `Card.stories.tsx`.
* **States:** Define at least a "Default" and "Empty/Loading" state.

---

## 4. Tech Stack Rules

### Python (Core/API/Web)

* **Manager:** Use `uv`.
* Install: `uv add <package>`
* Run: `uv run <script>`


* **Linting:** Use `ruff`. Run `uv run ruff check .` before finishing a task.
* **Database:** Use `FastLite`.

### Documentation (Quarto)

* **Tool:** Quarto.
* **Usage:** Use notebooks in `docs/tutorials` to document complex `core` logic.

---

## 5. Common Commands

* **Install All:** `uv sync`
* **Start Streamlit:** `cd interfaces/dashboard && uv run streamlit run app.py`
* **Start FastHTML:** `cd interfaces/web && uv run python main.py`
* **Start React:** `cd interfaces/client && npm run dev`
* **Start API:** `cd interfaces/api && uv run uvicorn main:app --reload`