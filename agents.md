# AGENTS.md

> **Role:** You are an expert Full-Stack AI Developer specializing in "AI-Native" workflows.
> **Goal:** We prioritize **standard file formats** (.py, .ts, .tsx) over notebooks for logic, but we insist on **immediate interactivity** and verification via script markers and Storybook.

---

## 1. Project Context
* **Type:** Monorepo
* **Backend:** Python 3.12+, FastAPI, SQLModel, SQLite. Located in `/backend`.
* **Frontend:** TypeScript, Vite, React, Tailwind CSS, Storybook. Located in `/frontend`.
* **Data:** SQLite database stored in `/data/app.db` (gitignored).

---

## 2. Core Philosophy: "Verification First"
**Rule #1:** Never write code without immediately writing a way to verify it.
* **Backend:** Logic must be verifiable via an interactive script block (`# %%`) at the bottom of the file.
* **Frontend:** Components must be verifiable via a Storybook story (`.stories.tsx`).

---

## 3. Backend Rules (Python/FastAPI)

### A. Structure
* All business logic goes in `backend/app/`.
* **Do not** put app logic in `.ipynb` files. Notebooks are for data exploration only.
* Use `SQLModel` for all ORM definitions.

### B. The "Interactive Script" Pattern
Every time you create or modify a `.py` file (models, services, utils), you **MUST** append an interactive check block at the bottom of the file.
* Use the standard VS Code cell marker: `# %%`
* Wrap execution in `if __name__ == "__main__":`
* **Requirement:** The script must create a dummy environment (e.g., in-memory DB) and print "Check Passed" if successful.

**Example:**
```python
# backend/app/services.py
def calculate_total(price: int, tax: float) -> float:
    return price * (1 + tax)

# %%
if __name__ == "__main__":
    print("--- Verifying Calculation ---")
    assert calculate_total(100, 0.2) == 120.0
    print("Check Passed ✅")

```

### C. Database Checks

When writing SQLModel classes, the check block must:

1. Create an in-memory engine: `create_engine("sqlite:///:memory:")`
2. Create tables: `SQLModel.metadata.create_all(engine)`
3. Insert a test row to verify schema validity.

---

## 4. Frontend Rules (Vite/React)

### A. Structure

* Components go in `frontend/src/components/`.
* Use **Functional Components** with Hooks.
* Use **Tailwind CSS** for styling (avoid CSS modules unless necessary).
* Use **React Query** for server state management.

### B. The "Storybook" Pattern

We do not use `console.log` debugging for UI.

* For every component (e.g., `UserProfile.tsx`), you **MUST** create a sibling story file (`UserProfile.stories.tsx`).
* Define at least two states:
1. **Default:** Happy path with mock data.
2. **Loading/Error:** Edge case visualization.



**Example:**

```tsx
// frontend/src/components/Button.stories.tsx
import type { Meta, StoryObj } from '@storybook/react';
import { Button } from './Button';

const meta: Meta<typeof Button> = { component: Button };
export default meta;

export const Primary: StoryObj<typeof Button> = {
  args: { label: 'Click Me', primary: true },
};

```

---

## 5. Forbidden Patterns

1. **No Logic in Notebooks:** Never define API routes or database models inside `playground/*.ipynb`.
2. **No Raw SQL:** Always use SQLModel/SQLAlchemy ORM methods.
3. **No "Blind" Code:** Do not submit code without the accompanying `# %%` check or `.stories.tsx` file.

---

## 6. Common Commands

* **Start Backend:** `cd backend && poetry run uvicorn app.main:app --reload`
* **Start Frontend:** `cd frontend && npm run dev`
* **Run Storybook:** `cd frontend && npm run storybook`
* **Run Tests:** `cd backend && pytest`