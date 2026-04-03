from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import logging
import os
import shutil
import sys
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
import re

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from core import service
    from core.cleanup_pages_urls import apply_pages_actions, plan_pages_cleanup
    from core.fastlite_db import bootstrap_scraper_db
    from core.llmapi_retrieval import extract_tab_step_anchors
    from core.llmapi_shared import (
        SOURCE_MIN_BM25_SCORE_RAW,
        SOURCE_MIN_VECTOR_SCORE_RAW,
    )
    from core.release_info import get_release_info
except ImportError:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core import service
    from core.cleanup_pages_urls import apply_pages_actions, plan_pages_cleanup
    from core.fastlite_db import bootstrap_scraper_db
    from core.llmapi_retrieval import extract_tab_step_anchors
    from core.llmapi_shared import (
        SOURCE_MIN_BM25_SCORE_RAW,
        SOURCE_MIN_VECTOR_SCORE_RAW,
    )
    from core.release_info import get_release_info

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "interfaces" / "client" / "dist"
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_SITE_DIR = DOCS_DIR / "_site"
HYBRID_RETRIEVAL_DOC = PROJECT_ROOT / "HYBRID_RETRIEVAL.md"
HYBRID_RETRIEVAL_QUARTO = PROJECT_ROOT / "docs" / "hybrid-retrieval.qmd"
HYBRID_RETRIEVAL_QUARTO_HTML = PROJECT_ROOT / "docs" / "hybrid-retrieval.html"

_LLMAPI = None
_SCRAPER_DB = None
_TAB_STEP_FRAGMENT_RE = re.compile(r"#tab-step(\d+)\b", re.IGNORECASE)
_LOGIN_CODE_RE = re.compile(r"^[A-Z0-9]{5,7}$")
_STREAM_ERROR_FALLBACK = "I hit a temporary problem generating a response. Please try again."
logger = logging.getLogger(__name__)


class ChatCreate(BaseModel):
    title: str | None = None


class ChatUpdate(BaseModel):
    title: str


class ProfileCreate(BaseModel):
    user_id: str


class MessageCreate(BaseModel):
    message: str


class FeedbackCreate(BaseModel):
    message_id: int
    rating: int
    note: str | None = None


def _load_llmapi():
    global _LLMAPI
    if _LLMAPI is not None:
        return _LLMAPI

    from core import llmapi as module
    _LLMAPI = module
    return module


def _get_scraper_db():
    global _SCRAPER_DB
    if _SCRAPER_DB is not None:
        return _SCRAPER_DB

    _SCRAPER_DB = bootstrap_scraper_db(seed=False)
    return _SCRAPER_DB


def _coerce_non_negative_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        return default
    return parsed if parsed >= 0 else default


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return float(default)


def _source_passes_source_score_gate(source: dict | None) -> bool:
    if not isinstance(source, dict):
        return False
    vector_raw = _coerce_float(source.get("vector_score_raw"))
    bm25_raw = _coerce_float(source.get("bm25_score_raw"))
    return (vector_raw > SOURCE_MIN_VECTOR_SCORE_RAW) or (bm25_raw > SOURCE_MIN_BM25_SCORE_RAW)


def _source_has_tab_step_fragment(source: dict | None) -> bool:
    if not isinstance(source, dict):
        return False
    for key in ("url", "url_canonical"):
        raw = str(source.get(key) or "").strip()
        if raw and _TAB_STEP_FRAGMENT_RE.search(raw):
            return True
    return False


def _hydrate_sources_with_last_scraped(sources: list[dict]) -> list[dict]:
    if not sources:
        return []

    try:
        db = _get_scraper_db()
    except Exception:
        db = None

    hydrated: list[dict] = []
    cache: dict[str, dict | None] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue

        item = dict(source)
        item["source_score_eligible"] = bool(_source_passes_source_score_gate(item))
        if not item["source_score_eligible"]:
            continue
        url = str(item.get("url") or item.get("url_canonical") or "").strip()
        if not url:
            hydrated.append(item)
            continue

        if db is not None and url not in cache:
            row = list(db.t.pages.rows_where("url=?", [url], limit=1))
            cache[url] = row[0] if row else None
        page_row = cache.get(url)

        has_tab_steps = bool(item.get("has_tab_steps")) or _source_has_tab_step_fragment(item)
        tab_step_count = _coerce_non_negative_int(item.get("tab_step_count"), default=0)
        if page_row:
            if not item.get("last_scraped") and page_row.get("last_scraped"):
                item["last_scraped"] = page_row.get("last_scraped")
            tab_step_anchors = extract_tab_step_anchors(page_row.get("html") or "")
            if tab_step_anchors:
                has_tab_steps = True
                tab_step_count = max(tab_step_count, len(tab_step_anchors))
        if has_tab_steps and tab_step_count <= 0:
            tab_step_count = 1
        item["has_tab_steps"] = bool(has_tab_steps)
        item["tab_step_count"] = int(tab_step_count)
        item["procedure_link_eligible"] = bool(has_tab_steps or _source_has_tab_step_fragment(item)) and bool(item["source_score_eligible"])
        hydrated.append(item)

    return hydrated


def _latest_site_last_crawled(site_id: int = 2) -> str:
    try:
        db = _get_scraper_db()
        rows = list(
            db.q(
                """
                SELECT last_scraped
                FROM pages
                WHERE site_id = ?
                  AND COALESCE(TRIM(last_scraped), '') <> ''
                ORDER BY last_scraped DESC
                LIMIT 1
                """,
                [site_id],
            )
        )
    except Exception:
        return ""

    if not rows:
        return ""
    value = rows[0].get("last_scraped")
    return str(value).strip() if value else ""


def _resolve_uvicorn_app_target() -> str:
    if __package__ == "interfaces.api":
        return "interfaces.api.main:app"
    if __package__ == "api":
        return "api.main:app"

    cwd = Path.cwd()
    if (cwd / "interfaces" / "api" / "main.py").exists():
        return "interfaces.api.main:app"
    if (cwd / "api" / "main.py").exists():
        return "api.main:app"
    if (cwd / "main.py").exists():
        return "main:app"

    candidates = ("interfaces.api.main", "api.main", "main")
    for module_name in candidates:
        if importlib.util.find_spec(module_name) is not None:
            return f"{module_name}:app"
    return "api.main:app"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _run_startup_url_cleanup(scraper_db) -> None:
    cleanup_enabled = _env_bool("HIERAG_URL_CLEANUP_ON_STARTUP", True)
    if not cleanup_enabled:
        print("Startup URL cleanup: disabled")
        return

    site_id = _env_int("HIERAG_URL_CLEANUP_SITE_ID", 2)
    drop_non_target = _env_bool("HIERAG_URL_CLEANUP_DROP_NON_TARGET", False)
    try:
        actions, stats = plan_pages_cleanup(
            scraper_db,
            site_id=site_id,
            drop_non_target=drop_non_target,
        )
        print(
            "Startup URL cleanup plan: "
            f"site_id={site_id} "
            f"planned_deletes={stats.get('planned_deletes', 0)} "
            f"planned_updates={stats.get('planned_updates', 0)}"
        )
        if not actions:
            print("Startup URL cleanup: no actions required")
            return

        applied = apply_pages_actions(scraper_db, actions)
        print(
            "Startup URL cleanup applied: "
            f"deleted={applied.get('deleted', 0)} "
            f"updated={applied.get('updated', 0)} "
            f"skipped={applied.get('skipped', 0)}"
        )

        try:
            llmapi = _load_llmapi()
            llmapi.refresh_retrieval_cache()
            print("Startup URL cleanup: retrieval cache refreshed")
        except Exception as exc:
            print(f"Startup URL cleanup: retrieval cache refresh skipped: {exc}")
    except Exception as exc:
        print(f"Startup URL cleanup skipped: {exc}")


def _run_startup_docs_render() -> None:
    render_enabled = _env_bool("HIERAG_RENDER_DOCS_ON_STARTUP", True)
    if not render_enabled:
        print("Startup docs render: disabled")
        return

    try:
        docs_ok = _render_docs_site_quarto()
        status = "ready" if docs_ok else "skipped_or_failed"
        print(f"Startup docs render: docs_site={status}")
    except Exception as exc:
        print(f"Startup docs render skipped: {exc}")


def _clean_ip(value: str) -> str:
    if not value:
        return ""

    val = value.strip().strip('"').strip("'")
    if "for=" in val:
        val = val.split("for=", 1)[1].split(";", 1)[0].strip()
    if "," in val:
        val = val.split(",", 1)[0].strip()

    val = val.strip().strip('"').strip("'")

    if val.startswith("["):
        end = val.find("]")
        if end != -1:
            return val[1:end]

    if val.count(":") == 1 and val.rsplit(":", 1)[1].isdigit():
        return val.rsplit(":", 1)[0]

    return val


def _client_ip(request: Request) -> str:
    override = _clean_ip(request.headers.get("x-profile-ip", ""))
    if override:
        return override

    header_candidates = [
        "x-forwarded-for",
        "x-original-forwarded-for",
        "x-real-ip",
        "x-client-ip",
        "forwarded",
    ]
    for header in header_candidates:
        ip = _clean_ip(request.headers.get(header, ""))
        if ip:
            return ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def _normalize_login_code(value: str) -> str:
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum()).upper()
    if _LOGIN_CODE_RE.fullmatch(cleaned):
        return cleaned
    return ""


def _avatar_from_user_id(user_id: str) -> dict[str, str]:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    color = f"hsl({hue} 65% 55%)"
    initials = (user_id or "U").upper()
    return {"color": color, "initials": initials}


def _login_code(request: Request) -> str:
    raw = request.headers.get("x-user-id", "")
    if not raw.strip():
        raise HTTPException(status_code=401, detail="Login required")

    login_code = _normalize_login_code(raw)
    if not login_code:
        raise HTTPException(status_code=400, detail="4+2 must be 5 to 7 letters and numbers")
    return login_code


def _user_id(request: Request) -> int:
    return service.get_or_create_user_by_login(_login_code(request), _client_ip(request))


def _admin_login_codes() -> set[str]:
    raw = os.getenv("HIERAG_ADMIN_LOGIN_CODES", "")
    admins = set()
    for item in raw.split(","):
        normalized = _normalize_login_code(item)
        if normalized:
            admins.add(normalized)
    return admins


def _is_admin_login(login_code: str) -> bool:
    return login_code in _admin_login_codes()


def _require_admin(request: Request) -> tuple[str, int]:
    login_code = _login_code(request)
    user_id = service.get_or_create_user_by_login(login_code, _client_ip(request))
    if not _is_admin_login(login_code):
        raise HTTPException(status_code=403, detail="Admin access required")
    return login_code, user_id


def _normalize_admin_range(value: str | None) -> str:
    normalized = str(value or "30d").strip().lower()
    if normalized in {"24h", "7d", "30d", "all", "custom"}:
        return normalized
    raise HTTPException(status_code=400, detail="Invalid admin time range")


def _validate_optional_datetime(value: str | None, label: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label} datetime") from exc
    return raw


def _normalize_stream_error_message(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return _STREAM_ERROR_FALLBACK
    if text.lower() in {"none", "unknown error", "unknown"}:
        return _STREAM_ERROR_FALLBACK
    return text


def _persist_assistant_stream_state(
    *,
    message_id: int,
    content: str,
    sources: list[dict[str, object]],
    debug_payload: dict | None,
    cache_id: object | None,
) -> None:
    service.update_message(
        message_id,
        content=content,
        sources_json=json.dumps(sources, ensure_ascii=True),
        debug_json=(
            json.dumps(debug_payload, ensure_ascii=True)
            if debug_payload is not None
            else None
        ),
        cached_from=cache_id,
    )


def _build_rewrite_history(chat_id: int, assistant_message_id: int, limit: int = 50) -> list[dict[str, object]]:
    rows = service.list_recent_messages(chat_id=chat_id, limit=limit)
    if not rows:
        return []

    assistant_message = service.get_message(assistant_message_id) or {}
    assistant_created_at = assistant_message.get("created_at")
    current_user_message_id = None
    if assistant_created_at:
        current_user = service.get_prev_user_message(chat_id, assistant_created_at)
        if current_user:
            current_user_message_id = current_user.get("id")

    history: list[dict[str, object]] = []
    for row in rows:
        if row.get("id") == assistant_message_id:
            continue
        if current_user_message_id is not None and row.get("id") == current_user_message_id:
            continue

        created_at = row.get("created_at") or ""
        if assistant_created_at and created_at and created_at > assistant_created_at:
            continue

        role = row.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = (row.get("content") or "").strip()
        if not content:
            continue
        item: dict[str, object] = {
            "role": role,
            "content": content,
            "message_id": row.get("id"),
        }
        if role == "assistant" and row.get("debug_json"):
            try:
                debug_payload = json.loads(row["debug_json"])
            except Exception:
                debug_payload = {}
            fallback_payload = debug_payload.get("fallback") if isinstance(debug_payload, dict) else None
            if isinstance(fallback_payload, dict):
                item["fallback_final_mode"] = fallback_payload.get("final_mode")
        history.append(item)

    return history


def _render_hybrid_doc_quarto() -> bool:
    if not HYBRID_RETRIEVAL_QUARTO.exists():
        return False

    quarto_bin = shutil.which("quarto")
    if not quarto_bin:
        print("Hybrid doc render skipped: quarto binary not found on PATH")
        return False

    if not HYBRID_RETRIEVAL_DOC.exists():
        return False

    needs_render = not HYBRID_RETRIEVAL_QUARTO_HTML.exists()
    if not needs_render:
        rendered_mtime = HYBRID_RETRIEVAL_QUARTO_HTML.stat().st_mtime
        source_mtime = HYBRID_RETRIEVAL_DOC.stat().st_mtime
        template_mtime = HYBRID_RETRIEVAL_QUARTO.stat().st_mtime
        needs_render = rendered_mtime < source_mtime or rendered_mtime < template_mtime

    if not needs_render:
        return True

    try:
        result = subprocess.run(
            [
                quarto_bin,
                "render",
                str(HYBRID_RETRIEVAL_QUARTO),
                "--to",
                "html",
                "--output",
                HYBRID_RETRIEVAL_QUARTO_HTML.name,
            ],
            cwd=str(HYBRID_RETRIEVAL_QUARTO.parent),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"Hybrid doc render failed: {exc}")
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            print(f"Hybrid doc render failed: {detail}")
        else:
            print("Hybrid doc render failed: unknown Quarto error")
        return False

    return HYBRID_RETRIEVAL_QUARTO_HTML.exists()


def _render_docs_site_quarto() -> bool:
    if not DOCS_DIR.exists():
        return False

    quarto_bin = shutil.which("quarto")
    if not quarto_bin:
        print("Docs render skipped: quarto binary not found on PATH")
        return False

    site_index = DOCS_SITE_DIR / "index.html"
    needs_render = not site_index.exists()
    if not needs_render:
        source_files = []
        for path in DOCS_DIR.rglob("*"):
            if not path.is_file():
                continue
            if DOCS_SITE_DIR in path.parents:
                continue
            if (DOCS_DIR / ".quarto") in path.parents:
                continue
            if path.suffix.lower() in {".qmd", ".md", ".ipynb", ".yml", ".yaml"}:
                source_files.append(path)

        if source_files:
            newest_source = max(path.stat().st_mtime for path in source_files)
            rendered_files = [path for path in DOCS_SITE_DIR.rglob("*") if path.is_file()]
            if not rendered_files:
                needs_render = True
            else:
                newest_rendered = max(path.stat().st_mtime for path in rendered_files)
                needs_render = newest_rendered < newest_source

    if not needs_render:
        return True

    def _run_render(cwd: Path) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                [quarto_bin, "render"],
                cwd=str(cwd),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "").strip()
        return True, ""

    ok, detail = _run_render(DOCS_DIR)
    if ok:
        return site_index.exists()

    if detail:
        print(f"Docs render failed (in-place): {detail}")
    else:
        print("Docs render failed (in-place): unknown Quarto error")

    # Fallback for environments where docs/.quarto is permission-locked.
    try:
        with tempfile.TemporaryDirectory(prefix="hierag-docs-render-") as tmp_root:
            tmp_project_root = Path(tmp_root)
            tmp_docs = tmp_project_root / "docs"
            shutil.copytree(
                DOCS_DIR,
                tmp_docs,
                ignore=shutil.ignore_patterns(".quarto", "_site"),
            )
            if HYBRID_RETRIEVAL_DOC.exists():
                shutil.copy2(HYBRID_RETRIEVAL_DOC, tmp_project_root / HYBRID_RETRIEVAL_DOC.name)
            ok_tmp, detail_tmp = _run_render(tmp_docs)
            if not ok_tmp:
                if detail_tmp:
                    print(f"Docs render failed (temp fallback): {detail_tmp}")
                else:
                    print("Docs render failed (temp fallback): unknown Quarto error")
                return False

            tmp_site = tmp_docs / "_site"
            if not tmp_site.exists():
                print("Docs render failed (temp fallback): _site output missing")
                return False

            if DOCS_SITE_DIR.exists():
                shutil.rmtree(DOCS_SITE_DIR)
            shutil.copytree(tmp_site, DOCS_SITE_DIR)
            return site_index.exists()
    except Exception as exc:
        print(f"Docs render failed (temp fallback exception): {exc}")
        return False


def _resolve_docs_site_file(doc_path: str) -> Path | None:
    if not DOCS_SITE_DIR.exists():
        return None

    normalized = doc_path.strip("/")
    if normalized == "hybrid-retrieval-doc":
        normalized = "hybrid-retrieval"

    candidates = ["index.html"] if not normalized else [
        normalized,
        f"{normalized}.html",
        f"{normalized}/index.html",
    ]

    site_root = DOCS_SITE_DIR.resolve()
    for candidate in candidates:
        resolved = (DOCS_SITE_DIR / candidate).resolve()
        if not resolved.is_relative_to(site_root):
            continue
        if resolved.is_file():
            return resolved

    return None


def _resolve_docs_root_file(doc_path: str) -> Path | None:
    if not DOCS_DIR.exists():
        return None

    normalized = doc_path.strip("/")
    if normalized == "hybrid-retrieval-doc":
        normalized = "hybrid-retrieval"

    candidates = [] if not normalized else [
        f"{normalized}.html",
        f"{normalized}.qmd",
        f"{normalized}/index.html",
        f"{normalized}/README.md",
    ]

    docs_root = DOCS_DIR.resolve()
    for candidate in candidates:
        resolved = (DOCS_DIR / candidate).resolve()
        if not resolved.is_relative_to(docs_root):
            continue
        if resolved.is_file():
            return resolved

    return None


def _inject_reference_link_rewrites(html_text: str) -> str:
    script = """
<script>
(function () {
  function canonicalize(pathname) {
    let rel = null;
    if (pathname === "/reference" || pathname === "/reference/") {
      rel = "";
    } else if (pathname.startsWith("/reference/")) {
      rel = pathname.slice("/reference/".length);
    } else if (pathname === "/connections/reference" || pathname === "/connections/reference/") {
      rel = "";
    } else if (pathname.startsWith("/connections/reference/")) {
      rel = pathname.slice("/connections/reference/".length);
    } else {
      return null;
    }

    if (rel.startsWith("site_libs/") || rel === "search.json") {
      return null;
    }
    if (rel.endsWith("index.html")) {
      rel = rel.slice(0, -("index.html".length));
    } else if (rel.endsWith(".html")) {
      rel = rel.slice(0, -(".html".length));
    }
    while (rel.startsWith("/")) {
      rel = rel.slice(1);
    }
    while (rel.endsWith("/")) {
      rel = rel.slice(0, -1);
    }
    return rel ? "/connections/reference/" + rel : "/connections/reference/";
  }

  const anchors = document.querySelectorAll("a[href]");
  anchors.forEach((a) => {
    const raw = a.getAttribute("href");
    if (!raw || raw.startsWith("#") || raw.startsWith("mailto:") || raw.startsWith("tel:")) {
      return;
    }

    let resolved;
    try {
      resolved = new URL(raw, window.location.href);
    } catch (err) {
      return;
    }
    if (resolved.origin !== window.location.origin) {
      return;
    }

    const canonical = canonicalize(resolved.pathname);
    if (!canonical) {
      return;
    }

    const finalHref = canonical + resolved.search + resolved.hash;
    a.setAttribute("href", finalHref);
    a.setAttribute("target", "_top");
  });
})();
</script>
""".strip()

    body_close = "</body>"
    if body_close in html_text:
        return html_text.replace(body_close, f"{script}\n{body_close}", 1)
    return f"{html_text}\n{script}"


def _serve_connections_docs(doc_path: str):
    if _render_docs_site_quarto():
        rendered = _resolve_docs_site_file(doc_path)
        if rendered is not None:
            if rendered.suffix.lower() == ".html":
                rendered_html = rendered.read_text(encoding="utf-8")
                patched_html = _inject_reference_link_rewrites(rendered_html)
                return HTMLResponse(patched_html)
            return FileResponse(rendered)

    fallback = _resolve_docs_root_file(doc_path)
    if fallback is not None:
        if fallback.suffix.lower() == ".html":
            fallback_html = fallback.read_text(encoding="utf-8")
            patched_html = _inject_reference_link_rewrites(fallback_html)
            return HTMLResponse(patched_html)
        if fallback.suffix.lower() in {".md", ".qmd"}:
            markdown_text = fallback.read_text(encoding="utf-8")
            escaped = html.escape(markdown_text)
            return HTMLResponse(
                "<!doctype html><html><head><meta charset='utf-8'/>"
                f"<title>{fallback.name}</title></head><body><pre>{escaped}</pre></body></html>"
            )
        return FileResponse(fallback)

    normalized = doc_path.strip("/")
    if normalized in {"", "hybrid-retrieval", "hybrid-retrieval-doc"}:
        return get_hybrid_retrieval_doc()

    raise HTTPException(status_code=404, detail="Documentation page not found")


app = FastAPI(title="hierag-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")


@app.on_event("startup")
def _startup() -> None:
    global _SCRAPER_DB
    service.create_db_and_tables()
    _run_startup_docs_render()
    # Avoid write-lock startup failures when scraper.db is shared by another process.
    seed_on_startup = os.getenv("HIERAG_SEED_SCRAPER_ON_STARTUP", "").lower() in {
        "1",
        "true",
        "yes",
    }
    _SCRAPER_DB = bootstrap_scraper_db(seed=seed_on_startup)
    _run_startup_url_cleanup(_SCRAPER_DB)


@api.get("/profile")
def profile(request: Request) -> dict[str, object]:
    user_id = _login_code(request)
    service.get_or_create_user_by_login(user_id, _client_ip(request))
    avatar = _avatar_from_user_id(user_id)
    return {"user_id": user_id, "avatar": avatar, "is_admin": _is_admin_login(user_id)}


@api.get("/release")
def release() -> dict[str, str]:
    payload = dict(get_release_info())
    payload["last_crawled"] = _latest_site_last_crawled(site_id=2)
    return payload


@api.get("/profiles")
def profiles() -> dict[str, object]:
    items = []
    for row in service.list_profiles(limit=100):
        user_id = str(row.get("login_code") or "").strip()
        if not user_id:
            continue
        items.append({"user_id": user_id, "avatar": _avatar_from_user_id(user_id)})
    return {"profiles": items}


@api.post("/profiles")
def create_profile(payload: ProfileCreate, request: Request) -> dict[str, bool]:
    user_id = _normalize_login_code(payload.user_id)
    if not user_id:
        raise HTTPException(status_code=400, detail="4+2 must be 5 to 7 letters and numbers")
    service.get_or_create_user_by_login(user_id, _client_ip(request))
    return {"ok": True}


@api.get("/chats")
def list_chats(request: Request) -> dict[str, object]:
    user_id = _user_id(request)
    chats = service.list_chats(user_id=user_id, limit=50)
    return {"chats": chats}


@api.post("/chats")
def create_chat(payload: ChatCreate, request: Request) -> dict[str, object]:
    user_id = _user_id(request)
    title = payload.title.strip() if payload.title else "New chat"
    chat_id = service.create_chat(user_id=user_id, title=title or "New chat")
    chats = service.list_chats(user_id=user_id, limit=1)
    chat = chats[0] if chats else {"id": chat_id, "title": title}
    return {"chat": chat}


@api.patch("/chats/{chat_id}")
def rename_chat(chat_id: int, payload: ChatUpdate, request: Request) -> dict[str, bool]:
    if not payload.title or not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is empty")
    user_id = _user_id(request)
    ok = service.rename_chat(chat_id, user_id, payload.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@api.delete("/chats/{chat_id}")
def remove_chat(chat_id: int, request: Request) -> dict[str, bool]:
    user_id = _user_id(request)
    ok = service.delete_chat(chat_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@api.get("/chats/{chat_id}/messages")
def list_messages(chat_id: int, request: Request, limit: int = 20) -> dict[str, object]:
    user_id = _user_id(request)
    if not service.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")

    rows = service.list_recent_messages(chat_id=chat_id, limit=limit)
    messages = []
    previous_user_created_at: str | None = None
    for row in rows:
        sources = []
        if row.get("sources_json"):
            try:
                sources = json.loads(row["sources_json"])
            except Exception:
                sources = []
        sources = _hydrate_sources_with_last_scraped(sources)
        created_at = row.get("created_at")
        question_created_at: str | None = None
        if row.get("role") == "assistant":
            question_created_at = previous_user_created_at or created_at
        elif row.get("role") == "user" and created_at:
            question_created_at = created_at
            previous_user_created_at = created_at
        messages.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "sources": sources,
                "has_debug": bool(row.get("debug_json")),
                "created_at": created_at,
                "question_created_at": question_created_at,
                "app_version": row.get("app_version"),
            }
        )
    return {"messages": messages}


@api.post("/chats/{chat_id}/messages")
def create_message(chat_id: int, payload: MessageCreate, request: Request) -> dict[str, object]:
    user_id = _user_id(request)
    if not service.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")

    release = get_release_info()
    app_version = str(release.get("version", "")).strip() or None
    question_norm = service.normalize_question(message)
    user_message_id = service.insert_message(
        chat_id=chat_id,
        role="user",
        content=message,
        question_norm=question_norm,
        app_version=app_version,
    )
    service.maybe_update_chat_title(chat_id, message)

    stream_id = uuid.uuid4().hex
    assistant_message_id = service.insert_message(
        chat_id=chat_id,
        role="assistant",
        content="",
        stream_id=stream_id,
        app_version=app_version,
    )
    user_row = service.get_message(user_message_id) or {}
    assistant_row = service.get_message(assistant_message_id) or {}

    return {
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "stream_id": stream_id,
        "user_created_at": user_row.get("created_at"),
        "assistant_created_at": assistant_row.get("created_at"),
        "question_created_at": user_row.get("created_at"),
        "app_version": assistant_row.get("app_version") or "",
    }


@api.post("/stream")
def stream(
    request: Request,
    message: str = Form(...),
    stream_id: str = Form(...),
    message_id: int = Form(...),
    chat_id: int = Form(1),
):
    _ = stream_id
    user_id = _user_id(request)
    client_ip = _client_ip(request)
    if not service.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")

    llmapi = _load_llmapi()
    history = _build_rewrite_history(chat_id=chat_id, assistant_message_id=message_id)

    def event_stream():
        full_text = ""
        sources: list[dict[str, object]] = []
        cache_id = None
        debug_payload = None

        try:
            for event in llmapi.stream_answer_with_context(
                message,
                top_k=10,
                max_extracts=6,
                history=history,
            ):
                etype = event.get("type")
                if etype == "delta":
                    delta = event.get("text", "")
                    full_text += delta
                    yield f"event: delta\ndata: {json.dumps({'text': delta}, ensure_ascii=True)}\n\n"
                elif etype == "sources":
                    sources = event.get("sources", [])
                    yield f"event: sources\ndata: {json.dumps({'sources': sources}, ensure_ascii=True)}\n\n"
                elif etype == "cache":
                    cache_id = event.get("cache_id")
                elif etype == "debug":
                    debug_payload = event.get("debug")
                    yield "event: debug\ndata: {\"available\": true}\n\n"
                elif etype == "error":
                    error_text = _normalize_stream_error_message(event.get("error"))
                    if not full_text.strip():
                        full_text = error_text
                    _persist_assistant_stream_state(
                        message_id=message_id,
                        content=full_text,
                        sources=sources,
                        debug_payload=debug_payload,
                        cache_id=cache_id,
                    )
                    logger.warning(
                        "Stream error event: chat_id=%s message_id=%s user_id=%s client_ip=%s error=%s",
                        chat_id,
                        message_id,
                        user_id,
                        client_ip,
                        error_text,
                    )
                    payload = {"error": error_text}
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                    return
                elif etype == "done":
                    _persist_assistant_stream_state(
                        message_id=message_id,
                        content=full_text,
                        sources=sources,
                        debug_payload=debug_payload,
                        cache_id=cache_id,
                    )
                    yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            error_text = _normalize_stream_error_message(str(exc))
            if not full_text.strip():
                full_text = error_text
            _persist_assistant_stream_state(
                message_id=message_id,
                content=full_text,
                sources=sources,
                debug_payload=debug_payload,
                cache_id=cache_id,
            )
            logger.exception(
                "Stream exception: chat_id=%s message_id=%s user_id=%s client_ip=%s",
                chat_id,
                message_id,
                user_id,
                client_ip,
            )
            payload = {"error": error_text}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api.get("/messages/{message_id}/debug")
def get_message_debug(message_id: int, request: Request) -> dict[str, object]:
    user_id = _user_id(request)
    msg = service.get_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if not service.chat_belongs_to_user(msg["chat_id"], user_id):
        raise HTTPException(status_code=404, detail="Message not found")

    if not msg.get("debug_json"):
        raise HTTPException(status_code=404, detail="Debug info not available")

    try:
        debug = json.loads(msg["debug_json"])
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise HTTPException(status_code=500, detail="Debug payload is invalid") from exc

    return {
        "message_id": msg["id"],
        "chat_id": msg["chat_id"],
        "created_at": msg.get("created_at"),
        "debug": debug,
    }


@api.get("/admin/stats/users")
def admin_user_stats(
    request: Request,
    range: str = "30d",
    start: str | None = None,
    end: str | None = None,
    user_id_search: str | None = None,
    sort: str = "last_interaction_at:desc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    admin_login, _ = _require_admin(request)
    time_range = _normalize_admin_range(range)
    start_value = _validate_optional_datetime(start, "start")
    end_value = _validate_optional_datetime(end, "end")
    payload = service.list_admin_user_stats(
        range_name=time_range,
        start=start_value,
        end=end_value,
        user_id_search=user_id_search,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return {
        "summary": payload["summary"],
        "users": payload["items"],
        "pagination": payload["pagination"],
        "filters": {
            "range": time_range,
            "start": start_value,
            "end": end_value,
            "user_id_search": str(user_id_search or "").strip(),
            "sort": sort,
        },
        "admin_user_id": admin_login,
    }


@api.get("/admin/interactions")
def admin_interactions(
    request: Request,
    range: str = "30d",
    start: str | None = None,
    end: str | None = None,
    user_id: str | None = None,
    rating: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    admin_login, _ = _require_admin(request)
    time_range = _normalize_admin_range(range)
    start_value = _validate_optional_datetime(start, "start")
    end_value = _validate_optional_datetime(end, "end")
    payload = service.list_admin_interactions(
        range_name=time_range,
        start=start_value,
        end=end_value,
        user_id=user_id,
        rating=rating,
        search=search,
        page=page,
        page_size=page_size,
    )
    return {
        "interactions": payload["items"],
        "pagination": payload["pagination"],
        "filters": {
            "range": time_range,
            "start": start_value,
            "end": end_value,
            "user_id": str(user_id or "").strip(),
            "rating": str(rating or "").strip() or "all",
            "search": str(search or "").strip(),
        },
        "admin_user_id": admin_login,
    }


@api.get("/admin/interactions/{message_id}")
def admin_interaction_detail(message_id: int, request: Request) -> dict[str, object]:
    admin_login, _ = _require_admin(request)
    row = service.get_admin_interaction(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Interaction not found")
    sources = _hydrate_sources_with_last_scraped(list(row.get("sources") or []))
    return {
        "interaction": {
            **row,
            "sources": sources,
            "debug_url": f"/?debug_message_id={message_id}",
        },
        "admin_user_id": admin_login,
    }


@api.get("/debug/hybrid-retrieval-doc")
def get_hybrid_retrieval_doc() -> HTMLResponse:
    if not HYBRID_RETRIEVAL_DOC.exists():
        raise HTTPException(status_code=404, detail="HYBRID_RETRIEVAL.md not found")

    if _render_hybrid_doc_quarto() and HYBRID_RETRIEVAL_QUARTO_HTML.exists():
        return HTMLResponse(HYBRID_RETRIEVAL_QUARTO_HTML.read_text(encoding="utf-8"))

    markdown_text = HYBRID_RETRIEVAL_DOC.read_text(encoding="utf-8")
    escaped = html.escape(markdown_text)
    markdown_json = json.dumps(markdown_text)
    page = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Hybrid Retrieval Guide</title>
  <script src=\"https://cdn.jsdelivr.net/npm/marked/marked.min.js\"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background: #f5f7fb;
      color: #1f2937;
      font-family: "Segoe UI", Tahoma, sans-serif;
    }}
    .wrap {{
      max-width: 1000px;
      margin: 24px auto;
      padding: 0 16px;
    }}
    .card {{
      background: #ffffff;
      border: 1px solid #dfe4ea;
      border-radius: 12px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
      overflow: hidden;
    }}
    .header {{
      padding: 14px 18px;
      border-bottom: 1px solid #e5e7eb;
      font-size: 18px;
      font-weight: 600;
    }}
    #doc {{
      padding: 18px;
      line-height: 1.6;
      font-size: 15px;
      color: #111827;
    }}
    #doc h1, #doc h2, #doc h3, #doc h4 {{
      margin-top: 1.2em;
      margin-bottom: 0.5em;
      line-height: 1.25;
    }}
    #doc p {{
      margin: 0.6em 0;
    }}
    #doc ul, #doc ol {{
      padding-left: 1.4em;
    }}
    #doc code {{
      background: #f3f4f6;
      border-radius: 4px;
      padding: 0.1em 0.35em;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 0.92em;
    }}
    #doc pre {{
      background: #f9fafb;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 12px;
      overflow-x: auto;
    }}
    #doc pre code {{
      background: transparent;
      padding: 0;
      border-radius: 0;
    }}
    #fallback {{
      margin: 0;
      padding: 18px;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.5;
      font-size: 14px;
      font-family: Consolas, "Cascadia Mono", monospace;
      background: #ffffff;
      display: none;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="header">Hybrid Retrieval Guide</div>
      <article id="doc"></article>
      <pre id="fallback">{escaped}</pre>
    </div>
  </div>
  <script>
    const source = {markdown_json};
    const target = document.getElementById("doc");
    const fallback = document.getElementById("fallback");
    if (window.marked && target) {{
      marked.setOptions({{
        breaks: true,
        gfm: true
      }});
      target.innerHTML = marked.parse(source);
    }} else {{
      fallback.style.display = "block";
    }}
  </script>
</body>
</html>
"""
    return HTMLResponse(page)


@app.get("/reference")
@app.get("/reference/")
@app.get("/connections/reference")
@app.get("/connections/reference/")
def connections_docs_index(request: Request):
    if not request.url.path.endswith("/"):
        return RedirectResponse(url=f"{request.url.path}/", status_code=307)

    return _serve_connections_docs("")


@app.get("/reference/{doc_path:path}")
@app.get("/connections/reference/{doc_path:path}")
def connections_docs(doc_path: str):
    return _serve_connections_docs(doc_path)


@app.get("/hybrid-retrieval-doc")
@app.get("/connections/hybrid-retrieval-doc")
def get_hybrid_retrieval_doc_legacy() -> RedirectResponse:
    return RedirectResponse(url="reference/hybrid-retrieval", status_code=307)


@api.post("/feedback")
def feedback(payload: FeedbackCreate, request: Request) -> dict[str, bool]:
    user_id = _user_id(request)
    msg = service.get_message(payload.message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if not service.chat_belongs_to_user(msg["chat_id"], user_id):
        raise HTTPException(status_code=404, detail="Message not found")

    service.insert_feedback(
        message_id=payload.message_id,
        user_id=user_id,
        rating=payload.rating,
        note=payload.note or "",
    )

    if msg.get("role") != "assistant":
        return {"ok": True}

    prev = service.get_prev_user_message(msg["chat_id"], msg["created_at"])
    if not prev:
        return {"ok": True}

    question = prev.get("content", "")
    sources = []
    if msg.get("sources_json"):
        try:
            sources = json.loads(msg["sources_json"])
        except Exception:
            sources = []
    sources = _hydrate_sources_with_last_scraped(sources)

    if payload.rating == 1:
        service.upsert_cache_good(question, msg.get("content", ""), sources)
    elif payload.rating == -1:
        service.update_cache_bad(question)

    return {"ok": True}


app.include_router(api)

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path == "health":
        return {"status": "ok"}

    if full_path.startswith("api"):
        raise HTTPException(status_code=404, detail="Not found")

    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    return HTMLResponse(
        "<h3>Frontend not built.</h3><p>Run the Vite dev server or build the frontend.</p>",
        status_code=503,
    )


# %%
if __name__ == "__main__":
    import argparse

    import uvicorn
    from fastapi.testclient import TestClient

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8510)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        service.create_db_and_tables()
        client = TestClient(app)
        response = client.get(
            "/api/profile",
            headers={"x-user-id": "1234AB", "x-forwarded-for": "127.0.0.1"},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == "1234AB"
        print("Check Passed")
    else:
        uvicorn.run(
            _resolve_uvicorn_app_target(),
            host="0.0.0.0",
            port=args.port,
            reload=args.reload,
        )
