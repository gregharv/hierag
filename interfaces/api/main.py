from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import os
import shutil
import sys
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from core import service
    from core.fastlite_db import bootstrap_scraper_db
except ImportError:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core import service
    from core.fastlite_db import bootstrap_scraper_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "interfaces" / "client" / "dist"
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_SITE_DIR = DOCS_DIR / "_site"
HYBRID_RETRIEVAL_DOC = PROJECT_ROOT / "HYBRID_RETRIEVAL.md"
HYBRID_RETRIEVAL_QUARTO = PROJECT_ROOT / "docs" / "hybrid-retrieval.qmd"
HYBRID_RETRIEVAL_QUARTO_HTML = PROJECT_ROOT / "docs" / "hybrid-retrieval.html"

_LLMAPI = None


class ChatCreate(BaseModel):
    title: str | None = None


class ChatUpdate(BaseModel):
    title: str


class ProfileCreate(BaseModel):
    ip: str


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


def _avatar_from_ip(ip: str) -> dict[str, str]:
    digest = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    color = f"hsl({hue} 65% 55%)"
    label = ip.split(".")[-1] if "." in ip else ip[:2]
    initials = (label or "IP")[:2].upper()
    return {"color": color, "initials": initials}


def _user_id(request: Request) -> int:
    return service.get_or_create_user_by_ip(_client_ip(request))


def _render_hybrid_doc_quarto() -> bool:
    if not HYBRID_RETRIEVAL_QUARTO.exists():
        return False

    quarto_bin = shutil.which("quarto")
    if not quarto_bin:
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
        subprocess.run(
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
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False

    return HYBRID_RETRIEVAL_QUARTO_HTML.exists()


def _render_docs_site_quarto() -> bool:
    if not DOCS_DIR.exists():
        return False

    quarto_bin = shutil.which("quarto")
    if not quarto_bin:
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

    try:
        subprocess.run(
            [quarto_bin, "render"],
            cwd=str(DOCS_DIR),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False

    return site_index.exists()


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
        if fallback.suffix.lower() == ".md":
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
    service.create_db_and_tables()
    # Avoid write-lock startup failures when scraper.db is shared by another process.
    seed_on_startup = os.getenv("HIERAG_SEED_SCRAPER_ON_STARTUP", "").lower() in {
        "1",
        "true",
        "yes",
    }
    bootstrap_scraper_db(seed=seed_on_startup)


@api.get("/profile")
def profile(request: Request) -> dict[str, object]:
    ip = _client_ip(request)
    avatar = _avatar_from_ip(ip)
    return {"ip": ip, "avatar": avatar}


@api.get("/profiles")
def profiles() -> dict[str, object]:
    items = []
    for row in service.list_profiles(limit=100):
        ip = row["ip"]
        items.append({"ip": ip, "avatar": _avatar_from_ip(ip)})
    return {"profiles": items}


@api.post("/profiles")
def create_profile(payload: ProfileCreate) -> dict[str, bool]:
    ip = payload.ip.strip() if payload.ip else ""
    if not ip:
        raise HTTPException(status_code=400, detail="IP is empty")
    service.get_or_create_user_by_ip(ip)
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
    for row in rows:
        sources = []
        if row.get("sources_json"):
            try:
                sources = json.loads(row["sources_json"])
            except Exception:
                sources = []
        messages.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "sources": sources,
                "has_debug": bool(row.get("debug_json")),
                "created_at": row.get("created_at"),
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

    question_norm = service.normalize_question(message)
    user_message_id = service.insert_message(
        chat_id=chat_id,
        role="user",
        content=message,
        question_norm=question_norm,
    )
    service.maybe_update_chat_title(chat_id, message)

    stream_id = uuid.uuid4().hex
    assistant_message_id = service.insert_message(
        chat_id=chat_id,
        role="assistant",
        content="",
        stream_id=stream_id,
    )

    return {
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "stream_id": stream_id,
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
    if not service.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")

    llmapi = _load_llmapi()

    def event_stream():
        full_text = ""
        sources: list[dict[str, object]] = []
        cache_id = None
        debug_payload = None

        try:
            for event in llmapi.stream_answer_with_context(message, top_k=10, max_extracts=6):
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
                    payload = {"error": event.get("error", "Unknown error")}
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                elif etype == "done":
                    service.update_message(
                        message_id,
                        content=full_text,
                        sources_json=json.dumps(sources, ensure_ascii=True),
                        debug_json=(
                            json.dumps(debug_payload, ensure_ascii=True)
                            if debug_payload is not None
                            else None
                        ),
                        cached_from=cache_id,
                    )
                    yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=True)}\n\n"

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
        response = client.get("/api/profile", headers={"x-profile-ip": "127.0.0.1"})
        assert response.status_code == 200
        print("Check Passed")
    else:
        uvicorn.run(
            _resolve_uvicorn_app_target(),
            host="0.0.0.0",
            port=args.port,
            reload=args.reload,
        )
