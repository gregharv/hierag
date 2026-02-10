from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import json
import hashlib
import uuid
import html
import app_db
import importlib.util

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
HYBRID_RETRIEVAL_DOC = BASE_DIR / "HYBRID_RETRIEVAL.md"

LLM_PATH = BASE_DIR / "05_llmapi.py"
_spec = importlib.util.spec_from_file_location("llmapi", LLM_PATH)
_llmapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llmapi)

app = FastAPI()
app_db.init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


api = APIRouter(prefix="/api")


def _clean_ip(value: str) -> str:
    if not value:
        return ""
    val = value.strip().strip('"').strip("'")

    # Handle Forwarded header format: for=1.2.3.4;proto=http
    if "for=" in val:
        val = val.split("for=", 1)[1].split(";", 1)[0].strip()

    # If multiple addresses, use the first
    if "," in val:
        val = val.split(",", 1)[0].strip()

    val = val.strip().strip('"').strip("'")

    # IPv6 in brackets, possibly with port: [::1]:1234
    if val.startswith("["):
        end = val.find("]")
        if end != -1:
            return val[1:end]

    # Strip port from IPv4:port
    if val.count(":") == 1 and val.rsplit(":", 1)[1].isdigit():
        return val.rsplit(":", 1)[0]

    return val


def _clean_ip(value: str) -> str:
    if not value:
        return ""
    val = value.strip().strip('"').strip("'")

    # Handle Forwarded header format: for=1.2.3.4;proto=http
    if "for=" in val:
        val = val.split("for=", 1)[1].split(";", 1)[0].strip()

    # If multiple addresses, use the first
    if "," in val:
        val = val.split(",", 1)[0].strip()

    val = val.strip().strip('"').strip("'")

    # IPv6 in brackets, possibly with port: [::1]:1234
    if val.startswith("["):
        end = val.find("]")
        if end != -1:
            return val[1:end]

    # Strip port from IPv4:port
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
        raw = request.headers.get(header, "")
        ip = _clean_ip(raw)
        if ip:
            return ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"

def _avatar_from_ip(ip: str) -> dict:
    digest = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    color = f"hsl({hue} 65% 55%)"
    label = ip.split(".")[-1] if "." in ip else ip[:2]
    initials = (label or "IP")[:2].upper()
    return {"color": color, "initials": initials}


def _user_id(request: Request) -> int:
    ip = _client_ip(request)
    return app_db.get_or_create_user_by_ip(ip)


@api.get("/profile")
def profile(request: Request):
    ip = _client_ip(request)
    avatar = _avatar_from_ip(ip)
    return {"ip": ip, "avatar": avatar}


@api.get("/profiles")
def profiles():
    items = []
    for row in app_db.list_profiles(limit=100):
        ip = row["ip"]
        items.append({"ip": ip, "avatar": _avatar_from_ip(ip)})
    return {"profiles": items}


@api.post("/profiles")
def create_profile(payload: ProfileCreate):
    ip = payload.ip.strip() if payload.ip else ""
    if not ip:
        raise HTTPException(status_code=400, detail="IP is empty")
    app_db.get_or_create_user_by_ip(ip)
    return {"ok": True}


@api.get("/chats")
def list_chats(request: Request):
    user_id = _user_id(request)
    chats = app_db.list_chats(user_id=user_id, limit=50)
    return {"chats": chats}


@api.post("/chats")
def create_chat(payload: ChatCreate, request: Request):
    user_id = _user_id(request)
    title = payload.title.strip() if payload.title else "New chat"
    chat_id = app_db.create_chat(user_id=user_id, title=title or "New chat")
    chats = app_db.list_chats(user_id=user_id, limit=1)
    chat = chats[0] if chats else {"id": chat_id, "title": title}
    return {"chat": chat}


@api.patch("/chats/{chat_id}")
def rename_chat(chat_id: int, payload: ChatUpdate, request: Request):
    if not payload.title or not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is empty")
    user_id = _user_id(request)
    ok = app_db.rename_chat(chat_id, user_id, payload.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@api.delete("/chats/{chat_id}")
def remove_chat(chat_id: int, request: Request):
    user_id = _user_id(request)
    ok = app_db.delete_chat(chat_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"ok": True}


@api.get("/chats/{chat_id}/messages")
def list_messages(chat_id: int, request: Request, limit: int = 20):
    user_id = _user_id(request)
    if not app_db.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    rows = app_db.list_recent_messages(chat_id=chat_id, limit=limit)
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
def create_message(chat_id: int, payload: MessageCreate, request: Request):
    user_id = _user_id(request)
    if not app_db.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")
    question_norm = app_db.normalize_question(message)
    user_message_id = app_db.insert_message(
        chat_id=chat_id,
        role="user",
        content=message,
        question_norm=question_norm,
    )
    app_db.maybe_update_chat_title(chat_id, message)
    stream_id = uuid.uuid4().hex
    assistant_message_id = app_db.insert_message(
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
    user_id = _user_id(request)
    if not app_db.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    def event_stream():
        full_text = ""
        sources = []
        cache_id = None
        debug_payload = None
        try:
            for event in _llmapi.stream_answer_with_context(
                message, top_k=10, max_extracts=6
            ):
                etype = event.get("type")
                if etype == "delta":
                    delta = event.get("text", "")
                    full_text += delta
                    payload = {"text": delta}
                    yield f"event: delta\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                elif etype == "sources":
                    sources = event.get("sources", [])
                    payload = {"sources": sources}
                    yield f"event: sources\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                elif etype == "cache":
                    cache_id = event.get("cache_id")
                elif etype == "debug":
                    debug_payload = event.get("debug")
                    yield "event: debug\ndata: {\"available\": true}\n\n"
                elif etype == "error":
                    payload = {"error": event.get("error", "Unknown error")}
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                elif etype == "done":
                    app_db.update_message(
                        message_id,
                        content=full_text,
                        sources_json=json.dumps(sources, ensure_ascii=True),
                        debug_json=json.dumps(debug_payload, ensure_ascii=True) if debug_payload is not None else None,
                        cached_from=cache_id,
                    )
                    yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            payload = {"error": str(exc)}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@api.get("/messages/{message_id}/debug")
def get_message_debug(message_id: int, request: Request):
    user_id = _user_id(request)
    msg = app_db.get_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if not app_db.chat_belongs_to_user(msg["chat_id"], user_id):
        raise HTTPException(status_code=404, detail="Message not found")
    if not msg.get("debug_json"):
        raise HTTPException(status_code=404, detail="Debug info not available")
    try:
        debug = json.loads(msg["debug_json"])
    except Exception:
        raise HTTPException(status_code=500, detail="Debug payload is invalid")
    return {
        "message_id": msg["id"],
        "chat_id": msg["chat_id"],
        "created_at": msg.get("created_at"),
        "debug": debug,
    }


@api.get("/debug/hybrid-retrieval-doc")
def get_hybrid_retrieval_doc():
    if not HYBRID_RETRIEVAL_DOC.exists():
        raise HTTPException(status_code=404, detail="HYBRID_RETRIEVAL.md not found")
    markdown_text = HYBRID_RETRIEVAL_DOC.read_text(encoding="utf-8")
    escaped = html.escape(markdown_text)
    markdown_json = json.dumps(markdown_text)
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Hybrid Retrieval Guide</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
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


@api.post("/feedback")
def feedback(payload: FeedbackCreate, request: Request):
    user_id = _user_id(request)
    msg = app_db.get_message(payload.message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if not app_db.chat_belongs_to_user(msg["chat_id"], user_id):
        raise HTTPException(status_code=404, detail="Message not found")
    app_db.insert_feedback(
        message_id=payload.message_id,
        user_id=user_id,
        rating=payload.rating,
        note=payload.note or "",
    )
    if msg.get("role") != "assistant":
        return {"ok": True}
    prev = app_db.get_prev_user_message(msg["chat_id"], msg["created_at"])
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
        app_db.upsert_cache_good(question, msg.get("content", ""), sources)
    elif payload.rating == -1:
        app_db.update_cache_bad(question)
    return {"ok": True}


app.include_router(api)


if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api"):
        raise HTTPException(status_code=404, detail="Not found")
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h3>Frontend not built.</h3><p>Run the Vite dev server or build the frontend.</p>",
        status_code=503,
    )


if __name__ == "__main__":
    import argparse
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8510)
    args = p.parse_args()
    uvicorn.run("app:app", host="0.0.0.0", port=args.port, reload=True)
